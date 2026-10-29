"""Local coding-session discovery and exact-ID dispatch. No browser or UI automation."""
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid

from . import __version__, locate, paths, store

PREFIX = 'desktop:direct:'
NAMES = {'codex': 'Codex', 'claude': 'Claude Code'}
LOCK = threading.RLock()
CACHE = None
CACHE_AT = 0
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')


def run_codex(args, timeout=15):
    exe = locate.tool_path('codex')
    if not exe:
        raise ValueError('Install Codex CLI or choose its executable in Settings.')
    with locate.external_runtime():
        return subprocess.run([str(exe), *args], capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class CodexReader:
    """Short-lived metadata client; never resumes a thread or takes over its runtime."""
    def __enter__(self):
        exe = locate.tool_path('codex')
        if not exe:
            raise ValueError('Install Codex CLI or choose its executable in Settings.')
        with locate.external_runtime():
            self.process = subprocess.Popen([str(exe), 'app-server', '--stdio'], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.messages = queue.Queue()
        self.counter = 0
        def reader():
            try:
                for line in self.process.stdout:
                    try:
                        self.messages.put(json.loads(line))
                    except ValueError:
                        pass
            finally:
                self.messages.put(None)
        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'arabic_relay', 'version': __version__},
                                     'capabilities': {'experimentalApi': True}})
            self.process.stdin.write('{"method":"initialized"}\n')
            self.process.stdin.flush()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def call(self, method, params):
        self.counter += 1
        self.process.stdin.write(json.dumps({'id': self.counter, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if message is None:
                raise ValueError('Codex session service closed. Check the Codex CLI installation.')
            if message.get('id') == self.counter:
                if 'error' in message:
                    raise ValueError('Codex could not read sessions. Update Codex CLI and refresh.')
                return message['result']
        raise ValueError('Codex session discovery timed out. Refresh to try again.')

    def __exit__(self, *_):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


def codex_sessions():
    rows = []
    cursor = None
    with CodexReader() as client:
        for _ in range(50):
            page = client.call('thread/list', {'limit': 100, 'sourceKinds': ['cli', 'vscode', 'appServer'],
                'sortKey': 'updated_at', 'useStateDbOnly': True, **({'cursor': cursor} if cursor else {})})
            rows.extend(t for t in page['data'] if not t.get('parentThreadId') and not t.get('ephemeral'))
            cursor = page.get('nextCursor')
            if not cursor:
                break
    running = json.loads(run_codex(['app-server', 'daemon', 'version']).stdout).get('status') == 'running'
    items = []
    for t in rows:
        if not UUID.fullmatch(t['id']):
            continue
        items.append({'provider': 'codex', 'sessionId': t['id'],
            'title': (t.get('name') or t.get('preview') or 'Untitled session')[:200],
            'cwd': t.get('cwd') or '', 'disabled': not running,
            'status': 'ready' if running else 'Start Codex service in Settings'})
    message = 'Direct local connection. Prompts queue in the selected session; open that session in Codex to process them.' if running else 'Start the local Codex service in Settings to send.'
    if cursor:
        message += ' Showing the 5,000 most recent sessions.'
    return items, message


def registration_folder():
    folder = paths.DATA / 'code-sessions'
    folder.mkdir(parents=True, exist_ok=True)
    return folder.resolve()


def pipe_available(address):
    if os.name != 'nt' or not isinstance(address, str) or not address.startswith('\\\\.\\pipe\\'):
        return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    return bool(kernel.WaitNamedPipeW(address, 0)) or ctypes.get_last_error() == 231  # busy is live


def registrations():
    found = {}
    for file in registration_folder().glob('*.bin'):
        try:
            data = json.loads(store.protect(file.read_bytes(), decrypt=True))
            sid = data['sessionId']
            if UUID.fullmatch(sid) and file.stem == sid and pipe_available(data['socket']):
                found[sid] = data
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def claude_sessions():
    live = registrations()
    items = {}
    archived = set()
    root = Path(os.environ.get('APPDATA', '')) / 'Claude/claude-code-sessions'
    for file in root.glob('*/*/local_*.json'):
        try:
            data = json.loads(file.read_text(encoding='utf-8'))
            sid = data.get('cliSessionId')
            if not isinstance(sid, str) or not UUID.fullmatch(sid):
                continue
            if data.get('isArchived'):
                archived.add(sid)
                continue
            items[sid] = {'provider': 'claude', 'sessionId': sid, 'desktopId': data.get('sessionId'),
                'title': (data.get('title') or 'Untitled session')[:200], 'cwd': data.get('cwd') or '',
                'disabled': sid not in live, 'status': 'ready' if sid in live else 'Reopen session after connecting Claude'}
        except (OSError, ValueError, TypeError):
            continue
    for sid, entry in live.items():
        if sid in archived:
            continue
        items.setdefault(sid, {'provider': 'claude', 'sessionId': sid,
            'title': 'Claude Code · ' + (Path(entry.get('cwd') or '.').name or sid[:8]),
            'cwd': entry.get('cwd') or '', 'disabled': False, 'status': 'ready'})
    return list(items.values()), ('Direct local inboxes. Existing sessions connect when reopened or when you next submit a prompt.'
        if hook_installed() else 'Click Connect Claude Code in Settings, then reopen the sessions you want to use.')


def catalog(force=False):
    global CACHE, CACHE_AT
    with LOCK:
        if not force and CACHE is not None and time.monotonic() - CACHE_AT < 10:
            return CACHE
        projects, apps = [], []
        for provider, discover in (('codex', codex_sessions), ('claude', claude_sessions)):
            try:
                items, message = discover()
            except Exception:
                items, message = [], ('Codex session discovery failed. Check its executable and refresh.' if provider == 'codex'
                                     else 'Claude Code session discovery failed. Reconnect in Settings.')
            threads = [{'id': PREFIX + provider + ':' + t['sessionId'], 'title': t['title'],
                'disabled': t['disabled'], 'status': t['status'], 'runtimeMode': t['cwd'] or 'Local coding session'} for t in items]
            projects.append({'id': PREFIX + provider, 'title': NAMES[provider], 'workspaceRoot': 'Direct local sessions',
                             'desktop': True, 'threads': threads, 'connectionStatus': message})
            apps.append({'name': NAMES[provider], 'ready': any(not t['disabled'] for t in items), 'message': message})
        CACHE = {'projects': projects, 'apps': apps}
        CACHE_AT = time.monotonic()
        return CACHE


def validate(target):
    provider = target['projectId'].removeprefix(PREFIX)
    sid = target['threadId'].removeprefix(PREFIX + provider + ':')
    if provider not in NAMES or target['projectId'] != PREFIX + provider or not UUID.fullmatch(sid) or target['threadId'] != PREFIX + provider + ':' + sid:
        raise ValueError('Invalid direct session. Refresh recipients.')
    data = catalog(force=True)
    project = next(p for p in data['projects'] if p['id'] == target['projectId'])
    item = next((t for t in project['threads'] if t['id'] == target['threadId'] and not t['disabled']), None)
    if item is None:
        raise ValueError('This session is unavailable. Refresh recipients and reconnect it in Settings.')
    return {'provider': provider, 'sessionId': sid, 'title': item['title']}


def write_pipe(address, payload):
    if not pipe_available(address):
        raise ValueError('Claude session is no longer connected. Reopen it and refresh.')
    # A live, session-provided Windows pipe. No remote pipes and no shell invocation.
    with open(address, 'r+b', buffering=0) as pipe:
        written = pipe.write(payload)
        if written != len(payload):
            raise ValueError('Claude inbox write was incomplete. Check the session before retrying.')


def transcript_has(path, message_id):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(max(0, stream.seek(0, 2) - 2_000_000))
            for line in stream:
                try:
                    row = json.loads(line)
                    if row.get('uuid') == message_id and row.get('type') == 'user':
                        return True
                except (ValueError, AttributeError):
                    pass
    except OSError:
        pass
    return False


def send(target, text):
    item = validate(target)
    message_id = str(uuid.uuid4())
    if item['provider'] == 'codex':
        # The durable queue preserves the selected thread. Never exec/resume a second agent.
        try:
            result = run_codex(['queue', '--thread', item['sessionId'], '--message', text], timeout=40)
        except subprocess.TimeoutExpired:
            raise ValueError('Codex did not confirm the queue request. Check the session; no automatic retry was made.') from None
        if result.returncode:
            raise ValueError('Codex rejected the queue request. Open the session in Codex and check its connection; no automatic retry was made.')
        return {'delivery': 'direct_submit', 'messageId': message_id, 'note': 'Codex accepted the prompt into its session queue. Open that session in Codex to process it.'}
    entry = registrations().get(item['sessionId'])
    if not entry:
        raise ValueError('Claude session disconnected. Reopen it and refresh.')
    lines = [{'type': 'auth', 'token': entry['token']}, {'type': 'user', 'uuid': message_id,
             'from': 'Arabic Relay', 'message': {'role': 'user', 'content': text}}]
    write_pipe(entry['socket'], ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in lines).encode('utf-8'))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if transcript_has(entry['transcript'], message_id):
            return {'delivery': 'direct_submit', 'messageId': message_id, 'note': 'Claude recorded the prompt in the selected session.'}
        time.sleep(.25)
    raise ValueError('The prompt was written to Claude’s local connection, but acceptance is not yet confirmed. It may be held or waiting for the current task. Check Claude; Relay will not retry automatically.')


def settings_path():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'settings.json'


def hook_command():
    folder = registration_folder().parent
    script = folder / 'relay-claude-hook.ps1'
    return subprocess.list2cmdline(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                                   '-File', str(script), '-RegistrationDirectory', str(registration_folder())])


def hook_installed():
    try:
        data = json.loads(settings_path().read_text(encoding='utf-8-sig'))
        return any(h.get('command') == hook_command() for group in data.get('hooks', {}).get('SessionStart', []) for h in group.get('hooks', []))
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def connect_claude():
    global CACHE
    folder = registration_folder().parent
    shutil.copy2(paths.ROOT / 'relay-claude-hook.ps1', folder / 'relay-claude-hook.ps1')
    config = settings_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(config.read_text(encoding='utf-8-sig')) if config.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
        raise ValueError('Claude settings are not valid. Repair settings.json before connecting.')
    if data.get('disableAllHooks') is True:
        raise ValueError('Claude hooks are disabled in your settings. Enable them in Claude before connecting Relay.')
    command = hook_command()
    hooks = data.setdefault('hooks', {})
    for event in ('SessionStart', 'UserPromptSubmit', 'SessionEnd'):
        groups = hooks.setdefault(event, [])
        if not any(h.get('command') == command for group in groups for h in group.get('hooks', [])):
            groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 5}]})
    if config.exists():
        backup = config.with_name('settings.before-arabic-relay-' + str(time.time_ns()) + '.json')
        shutil.copy2(config, backup)
    temp = config.with_name('settings.relay-' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(config)
    CACHE = None
    return {'message': 'Claude Code connected. Reopen existing sessions, or submit your next prompt there, then refresh Relay.'}


def connect_codex():
    global CACHE
    result = run_codex(['app-server', 'daemon', 'start'], timeout=30)
    if result.returncode:
        raise ValueError('Codex service could not start. Update Codex CLI and try again.')
    CACHE = None
    return {'message': 'Codex local session service is running.'}
