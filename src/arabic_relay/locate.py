"""Find outside tools and the Whisper model: the usual places first, then what the user picked."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import shutil
import sys
import winreg

from . import paths, store

HF_REPOS = ('models--Systran--faster-whisper-large-v3', 'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo')
APP_MODELS = ('large-v3', 'large-v3-turbo')  # downloaded by the app into paths.DATA/models
TOOLS = ('codex', 'claude', 'wispr')
UNINSTALL = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
FORMAT_HELP = 'Arabic Relay needs the faster-whisper version (Systran/faster-whisper-large-v3).'


def validate_model_folder(folder):
    folder = Path(folder)
    snapshots = folder / 'snapshots'
    if snapshots.is_dir():
        for snapshot in sorted(snapshots.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                return validate_model_folder(snapshot)
            except ValueError:
                continue
    if folder.is_dir():
        if any(folder.glob('*.pt')):
            raise ValueError('This is an OpenAI Whisper .pt model. ' + FORMAT_HELP)
        if any(folder.glob('ggml*.bin')):
            raise ValueError('This is a whisper.cpp model. ' + FORMAT_HELP)
        if all((folder / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json')) and (
                (folder / 'vocabulary.json').is_file() or (folder / 'vocabulary.txt').is_file()):
            return folder
    raise ValueError("This folder isn't a faster-whisper model. Choose the folder that contains model.bin and config.json.")


def hf_cache():
    if os.environ.get('HF_HUB_CACHE'):
        return Path(os.environ['HF_HUB_CACHE'])
    if os.environ.get('HF_HOME'):
        return Path(os.environ['HF_HOME']) / 'hub'
    return Path.home() / '.cache/huggingface/hub'


def whisper_model(saved):
    found = [paths.DATA / 'models' / name for name in APP_MODELS] + [hf_cache() / repo for repo in HF_REPOS]
    for folder in ([saved] if saved else []) + found:
        try:
            return validate_model_folder(folder)
        except (ValueError, OSError):
            continue
    return None


def translator_path():
    # Use the native executable; npm's .cmd shim requires a shell on Windows.
    installed = shutil.which('codex.exe')
    npm = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@openai/codex/node_modules'
    candidates = [npm / '@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe',
                  Path.home() / '.local/bin/codex.exe', winget_links() / 'codex.exe']
    return installed or next((str(p) for p in candidates if p.is_file()), None)


def claude_path():
    candidates = [Path.home() / '.local/bin/claude.exe', winget_links() / 'claude.exe']
    return shutil.which('claude.exe') or next((str(p) for p in candidates if p.is_file()), None)


def wispr_flow_path():
    roots = [Path(os.environ.get('LOCALAPPDATA', '')) / 'WisprFlow',
             Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Wispr Flow']
    roots += [Path(os.environ[name]) / 'Wispr Flow' for name in ('ProgramFiles', 'ProgramFiles(x86)') if os.environ.get(name)]
    roots += registry_install_folders('Wispr Flow')  # custom install folders
    for root in roots:
        candidates = [root / 'Wispr Flow.exe', root / 'WisprFlow.exe']
        candidates += sorted(root.glob('app-*/Wispr Flow.exe'), key=lambda p: p.stat().st_mtime, reverse=True)
        for candidate in candidates:
            if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()):
                return candidate
    return None


def winget_links():
    return Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/WinGet/Links'


def registry_install_folders(name):
    """InstallLocation of every installed program whose DisplayName is `name` (Windows "Installed apps")."""
    folders = []
    for hive, key_path in ((winreg.HKEY_CURRENT_USER, UNINSTALL), (winreg.HKEY_LOCAL_MACHINE, UNINSTALL),
                           (winreg.HKEY_LOCAL_MACHINE, UNINSTALL.replace('Software', 'Software\\WOW6432Node', 1))):
        try:
            root = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        index = 0
        while True:
            try:
                entry = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                key = winreg.OpenKey(root, entry)
                if winreg.QueryValueEx(key, 'DisplayName')[0] == name:
                    location = winreg.QueryValueEx(key, 'InstallLocation')[0]
                    if isinstance(location, str) and location.strip():
                        folders.append(Path(location.strip()))
            except OSError:
                continue
    return folders


AUTO_DETECT = {'codex': 'translator_path', 'claude': 'claude_path', 'wispr': 'wispr_flow_path'}


def check_kind(kind):
    if kind not in TOOLS:
        raise ValueError('Choose Codex, Claude Code or Wispr Flow.')


def tool_path(kind):
    """The program the user chose with Browse… while it still exists, else the one found in the usual places."""
    check_kind(kind)
    saved = store.get_setting(kind + '_path')
    if saved and Path(saved).is_file():
        return Path(saved)
    found = globals()[AUTO_DETECT[kind]]()
    return Path(found) if found else None


def set_tool_path(kind, path):
    check_kind(kind)
    if not isinstance(path, str) or len(path) > 1000:
        raise ValueError('Choose the program file (.exe).')
    if not path.strip():
        store.set_setting(kind + '_path', '')
        return {'kind': kind, 'path': None}
    program = Path(path.strip())
    if program.suffix.lower() != '.exe' or not program.is_file():
        raise ValueError('Choose the program file (.exe).')
    store.set_setting(kind + '_path', str(program))
    return {'kind': kind, 'path': str(program)}


def missing_tools():
    """Browse… choices whose program was moved or deleted since."""
    return {kind: saved for kind in TOOLS if (saved := store.get_setting(kind + '_path')) and not Path(saved).is_file()}


@contextmanager
def external_runtime():
    """External programs must not inherit a frozen app's Python DLL directory."""
    frozen = os.name == 'nt' and getattr(sys, 'frozen', False)
    if frozen:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        yield
    finally:
        if frozen:
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
