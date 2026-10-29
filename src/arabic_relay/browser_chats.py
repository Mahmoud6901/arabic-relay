"""Paired, loopback-only browser companion. Metadata is volatile; sends are leased once."""
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from . import paths, store

LOCK = threading.RLock()
PAIR = None
TABS = {}
JOBS = {}
TTL = 95
PREFIX = 'desktop:browser:'
NAMES = {'chatgpt': 'ChatGPT', 'claude': 'Claude'}
UUID = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'


def address(value):
    """Accept only observed private conversation routes, never share or arbitrary URLs."""
    if not isinstance(value, str) or len(value) > 600:
        return None
    try:
        u = urlsplit(value)
    except ValueError:
        return None
    if u.scheme != 'https' or u.query or u.fragment or u.username or u.password:
        return None
    provider = {'chatgpt.com': 'chatgpt', 'claude.ai': 'claude'}.get(u.netloc)
    if not provider:
        return None
    patterns = ([rf'/(?:g/[A-Za-z0-9_-]+/)?c/({UUID})'] if provider == 'chatgpt' else
                [rf'/chat/({UUID})', r'/cowork/(cse_[A-Za-z0-9]{10,80})', r'/code/(session_[A-Za-z0-9]{10,80})'])
    for pattern in patterns:
        m = re.fullmatch(pattern, u.path)
        if m:
            identity = provider + ':' + (m[1].lower() if re.fullmatch(UUID, m[1]) else m[1])
            return {'provider': provider, 'url': value, 'id': PREFIX + identity,
                    'kind': 'Code' if '/code/' in u.path else 'Work' if '/cowork/' in u.path else 'Chat'}
    return None


def extension_origin(origin):
    return isinstance(origin, str) and re.fullmatch(r'chrome-extension://[a-p]{32}', origin) is not None


def _credentials():
    try:
        return json.loads(store.protect((paths.DATA / 'browser.bin').read_bytes(), decrypt=True))
    except (OSError, ValueError):
        return None


def authorize(origin, token):
    saved = _credentials()
    return bool(extension_origin(origin) and saved and saved['origin'] == origin
                and isinstance(token, str) and secrets.compare_digest(saved['token'], token))


def start_pairing():
    global PAIR
    with LOCK:
        PAIR = {'code': secrets.token_urlsafe(24), 'expires': time.time() + 600}
        return {'code': PAIR['code'], 'expiresIn': 600}


def pair(origin, code):
    global PAIR
    with LOCK:
        if not extension_origin(origin) or not PAIR or time.time() > PAIR['expires'] or not isinstance(code, str) or not secrets.compare_digest(PAIR['code'], code):
            raise ValueError('Pairing code is invalid or expired. Generate a new code in Relay Settings.')
        token = secrets.token_urlsafe(32)
        (paths.DATA / 'browser.bin').write_bytes(store.protect(json.dumps({'origin': origin, 'token': token}).encode()))
        PAIR = None
        TABS.clear()
        _cancel_jobs()
        return {'token': token}


def _cancel_jobs():
    for job in JOBS.values():
        job['result'] = {'error': 'Browser connection changed. Check the conversation before sending again.'}
        job['event'].set()
    JOBS.clear()


def disconnect():
    global PAIR
    with LOCK:
        (paths.DATA / 'browser.bin').unlink(missing_ok=True)
        PAIR = None
        TABS.clear()
        _cancel_jobs()
    return {'disconnected': True}


def sync(body):
    """Replace a complete live-tab snapshot and lease pending commands exactly once."""
    tabs = body.get('tabs')
    if not isinstance(tabs, list) or len(tabs) > 100:
        raise ValueError('Invalid browser snapshot.')
    now = time.time()
    clean = {}
    for tab in tabs:
        if not isinstance(tab, dict) or type(tab.get('tabId')) is not int or not isinstance(tab.get('chats'), list) or len(tab['chats']) > 2000:
            raise ValueError('Invalid browser tab.')
        for chat in tab['chats']:
            if not isinstance(chat, dict):
                continue
            item = address(chat.get('url'))
            title = chat.get('title')
            if item and isinstance(title, str) and title.strip():
                clean[item['id']] = {**item, 'title': title.strip()[:240], 'tabId': tab['tabId'],
                                     'offline': chat.get('offline') is True, 'seen': now}
    with LOCK:
        TABS.clear()
        TABS.update(clean)
        commands = []
        for key, job in list(JOBS.items()):
            if now > job['expires']:
                job['event'].set()
                del JOBS[key]
            elif not job['claimed']:
                job['claimed'] = True
                commands.append({'id': key, 'url': job['url'], 'text': job['text'], 'expires': job['expires']})
        return {'commands': commands}


def complete(body):
    with LOCK:
        job = JOBS.get(body.get('id'))
        if not job or not job['claimed'] or job['event'].is_set() or time.time() > job['expires']:
            raise ValueError('This browser send expired. It will not be retried.')
        if body.get('submitted') is True and body.get('url') == job['url']:
            job['result'] = {'delivery': 'browser_submit', 'messageId': body['id'],
                             'note': 'Browser Send was clicked once. Check the conversation for its response.'}
        else:
            error = body.get('error')
            job['result'] = {'error': error[:600] if isinstance(error, str) else 'Browser submission was not verified.'}
        job['event'].set()
    return {'received': True}


def validate(target):
    with LOCK:
        item = TABS.get(target['threadId'])
        if not item or item['seen'] < time.time() - TTL or target['projectId'] != PREFIX + item['provider']:
            raise ValueError('Browser chat is no longer connected. Keep the companion and chat site open, then refresh.')
        if item['offline']:
            raise ValueError('This coding session is offline. Reconnect it in Claude before sending.')
        return dict(item)


def send(target, text):
    item = validate(target)
    key = secrets.token_urlsafe(24)
    job = {'url': item['url'], 'text': text, 'expires': time.time() + 60,
           'claimed': False, 'event': threading.Event(), 'result': None}
    with LOCK:
        JOBS[key] = job
    try:
        job['event'].wait(60)
        result = job['result']
        if not result:
            raise ValueError('Browser did not confirm submission. Check the conversation; Relay will not retry automatically.')
        if result.get('error'):
            raise ValueError(result['error'])
        return result
    finally:
        with LOCK:
            JOBS.pop(key, None)


def catalog():
    paired = _credentials() is not None
    projects, apps = [], []
    with LOCK:
        items = [dict(t) for t in TABS.values() if t['seen'] >= time.time() - TTL] if paired else []
    for provider, name in NAMES.items():
        threads = [{'id': t['id'], 'title': t['title'], 'status': 'offline' if t['offline'] else 'ready',
                    'disabled': t['offline'], 'runtimeMode': 'Browser · ' + t['kind']} for t in items if t['provider'] == provider]
        reason = (f'{len(threads)} conversations discovered from browser pages. Older chats appear when loaded in the site.' if threads else
                  'Open this site in the paired browser to discover conversations.' if paired else
                  'Connect the browser companion in Settings to discover conversations automatically.')
        projects.append({'id': PREFIX + provider, 'title': name, 'workspaceRoot': 'Browser conversations',
                         'desktop': True, 'threads': threads, 'connectionStatus': reason})
        apps.append({'name': name + ' browser', 'ready': bool(threads), 'message': reason})
    return {'projects': projects, 'apps': apps, 'paired': paired}
