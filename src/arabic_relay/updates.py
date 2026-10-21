"""Daily update check against GitHub Releases, background download, Ed25519 verification and a silent install."""
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__, desktop_chats, firstrun, locate, models, paths, speech, store, t3, translate

REPO = 'Mahmoud6901/arabic-relay'
PUBLIC_KEY = 'db597f49194c827e9ef9ec3df3e38d43000e088b576e72b7be8f3b1e43498436'  # Ed25519, raw 32 bytes as hex; the private key never leaves the owner's computer
API = f'https://api.github.com/repos/{REPO}/releases/latest'
MODES = ('auto', 'notify', 'off')
DAY = 24 * 3600
BUSY = 'Finish recording, transcribing, translating or sending first, then update.'
FLAGS = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS']
STATE = {'available': None, 'releaseUrl': None, 'failed': None}
RELEASES = f'https://github.com/{REPO}/releases'


def release_page(url):
    """The banner's links open in the system shell: only this repo's release pages get there."""
    return url if isinstance(url, str) and re.fullmatch(re.escape(RELEASES) + r'/tag/v\d+\.\d+\.\d+', url) else RELEASES


def folder():
    path = paths.DATA / 'updates'
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_version(text):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', text or '')
    if not match:
        raise ValueError(f'Not a release version: {text!r}')
    return tuple(int(part) for part in match.groups())


def newer(latest, current):
    return parse_version(latest) > parse_version(current)


def verify(file, signature, public_key=None):
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key or PUBLIC_KEY))
        key.verify(signature, Path(file).read_bytes())
        return True
    except (InvalidSignature, ValueError, OSError):
        return False


def mode():
    value = str(store.get_setting('update_mode', 'auto'))
    return value if value in MODES else 'auto'


def install_on_quit_enabled():
    return str(store.get_setting('update_install_on_quit', 1)) == '1'


def set_preferences(new_mode, install_on_quit):
    if new_mode not in MODES or type(install_on_quit) is not bool:
        raise ValueError('Choose Automatic, Notify only or Off.')
    store.set_setting('update_mode', new_mode)
    store.set_setting('update_install_on_quit', int(install_on_quit))
    return state()


def read_json(name):
    try:
        data = json.loads((folder() / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def ready():
    """The verified download waiting to install, or None."""
    info = read_json('ready.json')
    name = str((info or {}).get('file', ''))
    if not info or not name or Path(name).name != name or not (folder() / name).is_file():
        return None
    return info


def state():
    info = ready()
    return {'mode': mode(), 'installOnQuit': install_on_quit_enabled(), 'available': STATE['available'],
            'ready': info['version'] if info else None, 'failed': STATE['failed'],
            'releaseUrl': release_page(STATE['releaseUrl'] or (info or {}).get('url'))}


def request(url):
    return urllib.request.Request(url, headers={'User-Agent': f'Arabic-Relay/{__version__}', 'Accept': 'application/vnd.github+json'})


def small_download(url):
    with urllib.request.urlopen(request(url), timeout=30) as response:
        return response.read(1 << 20)


def download(release, version):
    name = f'ArabicRelay-Setup-{version}.exe'
    target, signature_file = folder() / name, folder() / (name + '.sig')
    for old in folder().iterdir():
        if not old.name.startswith(name.removesuffix('.exe')):
            old.unlink()  # earlier versions and their markers
    try:
        assets = {asset['name']: asset for asset in release.get('assets', [])}
        if not {name, name + '.sig', 'SHA256SUMS.txt'} <= set(assets):
            raise ValueError('the release is missing its installer, signature or checksums')
        sums = small_download(assets['SHA256SUMS.txt']['browser_download_url']).decode()
        digest = next(line.split()[0].lower() for line in sums.splitlines() if line.split()[1:] == [name])
        signature = small_download(assets[name + '.sig']['browser_download_url'])
        models.fetch(assets[name]['browser_download_url'], target, digest, assets[name]['size'], lambda *_: None)
        if len(signature) != 64 or not verify(target, signature):
            raise ValueError('the installer failed its signature check')
        signature_file.write_bytes(signature)
        (folder() / 'ready.json').write_text(json.dumps({'version': version, 'file': name, 'url': release.get('html_url')}), encoding='utf-8')
    except Exception as error:
        logging.warning('Update %s was not kept: %s', version, error)
        for leftover in (target, signature_file, target.with_suffix('.part')):
            leftover.unlink(missing_ok=True)


def check():
    store.set_setting('update_checked', time.time())
    with urllib.request.urlopen(request(API), timeout=30) as response:
        release = json.load(response)
    version = str(release.get('tag_name', '')).removeprefix('v')
    if not newer(version, __version__):
        STATE.update(available=None)
        return state()
    STATE.update(available=version, releaseUrl=release_page(release.get('html_url')))
    if mode() == 'auto' and (ready() or {}).get('version') != version:
        download(release, version)
    return state()


def due():
    try:
        last = float(store.get_setting('update_checked', 0))
    except (TypeError, ValueError):
        last = 0
    return mode() != 'off' and time.time() - last >= DAY


def busy():
    for lock in (speech.TRANSCRIBE_LOCK, translate.TRANSLATE_LOCK, t3.SEND_LOCK, desktop_chats.LOCK):
        if not lock.acquire(blocking=False):
            return True
        lock.release()
    return firstrun.JOB.state['state'] in ('downloading', 'checking')


def install(relaunch):
    info = ready()
    if not info:
        raise ValueError('No update is ready.')
    if busy():
        raise ValueError(BUSY)
    exe = folder() / info['file']
    signature = exe.with_name(exe.name + '.sig')
    if not (signature.is_file() and verify(exe, signature.read_bytes())):
        for leftover in (exe, signature, folder() / 'ready.json'):
            leftover.unlink(missing_ok=True)
        raise ValueError('The update failed its signature check and was deleted. Arabic Relay downloads it again later.')
    (folder() / 'attempt.json').write_text(json.dumps({'version': info['version']}), encoding='utf-8')
    env = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}  # the reopened app is its own program, not our child
    with locate.external_runtime():
        subprocess.Popen([str(exe), *FLAGS, *(['/RELAUNCH=1'] if relaunch else [])], close_fds=True, env=env,
                         creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    STATE['failed'] = None
    return {'installing': info['version']}


def install_on_quit():
    try:
        info, attempt = ready(), read_json('attempt.json')
        if not install_on_quit_enabled() or not info or (attempt or {}).get('version') == info['version']:
            return
        install(False)
    except Exception:
        logging.exception('Install on quit failed')


def startup():
    """After an install attempt: success clears the downloads; an older running version means it failed."""
    attempt = read_json('attempt.json')
    if attempt is None:
        return
    try:
        failed = newer(str(attempt.get('version')), __version__)
    except ValueError:
        failed = False
    if failed:
        STATE['failed'] = attempt['version']
        return
    for old in folder().iterdir():
        try:
            old.unlink()
        except OSError:
            pass  # Setup may still be running; the next launch removes it


def start_background():
    try:
        startup()
    except Exception:
        logging.exception('Update cleanup failed')

    def loop():
        while True:
            try:
                if due():
                    check()
            except Exception as error:
                logging.warning('Update check failed: %s', error)
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True, name='updates').start()
