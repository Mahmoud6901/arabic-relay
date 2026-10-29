"""T3 Code client (OAuth + MCP) and the one-click send pipeline to T3 sessions and desktop chats."""
import json
import sqlite3
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import browser_chats, desktop_chats, history, paths, store


T3 = 'http://127.0.0.1:3773'


SEND_LOCK = threading.RLock()


OAUTH = {}


MCP_SESSION = None


MCP_PROTOCOL = None


def load_auth():
    path = paths.DATA / 't3.bin'
    return json.loads(store.protect(path.read_bytes(), True)) if path.exists() else None


def save_auth(auth):
    path = paths.DATA / 't3.bin'
    temporary = paths.DATA / 't3.tmp'
    temporary.write_bytes(store.protect(json.dumps(auth).encode()))
    temporary.replace(path)


def request_json(url, data=None, headers=None, form=False):
    body = None if data is None else (urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode())
    hs = {'Accept': 'application/json, text/event-stream', **(headers or {})}
    if body is not None:
        hs['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    req = urllib.request.Request(url, body, hs)
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            raw = response.read().decode()
            if not raw:
                parsed = None
            elif response.headers.get('Content-Type', '').startswith('text/event-stream'):
                events = [json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith('data:')]
                parsed = next((x for x in events if 'result' in x or 'error' in x), None)
            else:
                parsed = json.loads(raw)
            return parsed, response.headers
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ValueError('T3 requires a new sign-in, or this connection lacks permission. Click Connect T3 and choose Full access.') from None
        raise ValueError(f'T3 returned HTTP {error.code}. No automatic retry was made.') from None
    except urllib.error.URLError:
        raise ValueError('T3 Code is not reachable. Open T3 Code on this computer and try Refresh.') from None


def access_token():
    with store.AUTH_LOCK:
        auth = load_auth()
        if not auth:
            raise ValueError('Connect this app to T3 first. Its sign-in is separate from Codex.')
        if auth.get('expires_at', 0) < time.time() + 60:
            if not auth.get('refresh_token'):
                raise ValueError('Your T3 sign-in expired. Click Connect T3.')
            token, _ = request_json(T3 + '/oauth/mcp/token', {
                'grant_type': 'refresh_token', 'refresh_token': auth['refresh_token'],
                'client_id': auth['client_id'], 'resource': T3 + '/mcp'}, form=True)
            auth.update(token)
            auth['expires_at'] = time.time() + token.get('expires_in', 3600)
            save_auth(auth)
        return auth['access_token']


def rpc(method, params=None, initialize=True):
    global MCP_SESSION, MCP_PROTOCOL
    with store.AUTH_LOCK:
        if initialize and MCP_SESSION is None:
            result, headers = request_json(T3 + '/mcp', {
                'jsonrpc': '2.0', 'id': secrets.randbelow(100000000), 'method': 'initialize',
                'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                           'clientInfo': {'name': 'Arabic Relay', 'version': '1.0.0'}}},
                {'Authorization': 'Bearer ' + access_token()})
            if not result or 'error' in result:
                raise ValueError('T3 could not initialize the connection.')
            protocol = result.get('result', {}).get('protocolVersion', '')
            if not isinstance(protocol, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', protocol):
                raise ValueError('T3 did not return a valid connection protocol.')
            session = headers.get('Mcp-Session-Id', '')
            ready_headers = {'Authorization': 'Bearer ' + access_token(), 'MCP-Protocol-Version': protocol}
            if session:
                ready_headers['Mcp-Session-Id'] = session
            request_json(T3 + '/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                         ready_headers)
            MCP_SESSION, MCP_PROTOCOL = session, protocol
        headers = {'Authorization': 'Bearer ' + access_token(), 'MCP-Protocol-Version': MCP_PROTOCOL or '2025-06-18'}
        if MCP_SESSION:
            headers['Mcp-Session-Id'] = MCP_SESSION
        result, _ = request_json(T3 + '/mcp', {'jsonrpc': '2.0', 'id': secrets.randbelow(100000000),
                                             'method': method, 'params': params or {}}, headers)
        if not result or 'error' in result:
            raise ValueError((result or {}).get('error', {}).get('message', 'T3 did not return a valid result.'))
        return result['result']


def tool(name, arguments):
    result = rpc('tools/call', {'name': name, 'arguments': arguments})
    if result.get('isError'):
        raise ValueError('\n'.join(c.get('text', '') for c in result.get('content', []) if c.get('type') == 'text')[:1500])
    if 'structuredContent' in result:
        return result['structuredContent']
    for item in result.get('content', []):
        if item.get('type') == 'text':
            try:
                return json.loads(item['text'])
            except json.JSONDecodeError:
                continue
    raise ValueError('T3 returned no structured result.')


def catalog():
    projects = []
    cursor = None
    for _ in range(20):
        page = tool('t3_project_list', {'limit': 100, **({'cursor': cursor} if cursor is not None else {})})
        projects.extend(page['projects'])
        cursor = page.get('nextCursor')
        if cursor is None:
            break
    answer = []
    for project in projects:
        if project.get('deletedAt'):
            continue
        threads = []
        cursor = None
        for _ in range(20):
            page = tool('t3_thread_list', {'projectId': project['id'], 'includeSubagents': False,
                                         'settled': False, 'limit': 100,
                                         **({'cursor': cursor} if cursor is not None else {})})
            threads.extend({'id': t['threadId'], 'title': t['title'], 'status': t['status'],
                            'runtimeMode': t['runtimeMode']} for t in page['threads'])
            cursor = page.get('nextCursor')
            if cursor is None:
                break
        answer.append({'id': project['id'], 'title': project['title'],
                       'workspaceRoot': project['workspaceRoot'], 'threads': threads})
    return answer


def normalize_payload(body):
    english = body.get('english')
    targets = body.get('targets')
    if not isinstance(english, str) or not english.strip() or len(english) > 16000:
        raise ValueError('Enter an English prompt between 1 and 16,000 characters.')
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        raise ValueError('Choose between 1 and 20 sessions.')
    seen = set()
    clean = []
    for target in targets:
        if not isinstance(target, dict) or not all(isinstance(target.get(k), str) and target[k] for k in ('projectId', 'threadId')):
            raise ValueError('Invalid recipient.')
        if target['threadId'] in seen:
            raise ValueError('Each session can receive the prompt only once.')
        seen.add(target['threadId'])
        clean.append({'projectId': target['projectId'], 'threadId': target['threadId']})
    return {'english': english.strip(), 'targets': clean}


def prepare_send(body, request_id=None):
    """Validate and freeze one send attempt; this is not a user approval step.

    The legacy approvals table is retained to preserve existing delivery receipts.
    """
    payload = normalize_payload(body)
    t3_targets = [target for target in payload['targets'] if not target['projectId'].startswith('desktop:')]
    mode = tool('orchestrator_capabilities', {})['runtimeMode'] if t3_targets else None
    levels = {'approval-required': 0, 'auto-accept-edits': 1, 'auto': 2, 'full-access': 3}
    for target in payload['targets']:
        if target['projectId'].startswith(browser_chats.PREFIX):
            item = browser_chats.validate(target)
            target['title'] = browser_chats.NAMES[item['provider']] + ' / ' + item['title']
            continue
        if target['projectId'].startswith('desktop:'):
            if os.environ.get('ARABIC_RELAY_NATIVE') != '1':
                raise ValueError('Desktop chat sending requires the native Arabic Relay application.')
            item = desktop_chats.validate(target)
            target['title'] = desktop_chats.NAMES[item['provider']] + ' / ' + item['title']
            continue
        thread = tool('t3_thread_read', {'threadId': target['threadId'], 'limit': 1, 'maxCharsPerItem': 1})['thread']
        if thread['projectId'] != target['projectId'] or thread['archived'] or thread['parentThreadId'] is not None:
            raise ValueError('A selected session no longer belongs to this project, or is unavailable. Refresh recipients.')
        if levels[thread['runtimeMode']] > levels[mode]:
            raise ValueError('A selected session uses broader permissions. Reconnect this app with Full access.')
        target.update(title=thread['title'], projectId=thread['projectId'])
    approval_id = request_id or secrets.token_urlsafe(24)
    with store.database() as db:
        db.execute('INSERT INTO approvals VALUES (?, ?, ?)', (approval_id, json.dumps(payload), time.time()))
    return {'requestId': approval_id, 'payload': payload}


def send_prompt(body):
    """One click dispatches the current prompt, with stable retry deduplication."""
    request_id = body.get('requestId')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', request_id):
        raise ValueError('Invalid send request. Refresh Relay and try again.')
    payload = normalize_payload(body)
    with SEND_LOCK:
        with store.database() as db:
            row = db.execute('SELECT payload FROM approvals WHERE id=?', (request_id,)).fetchone()
        if row:
            if normalize_payload(json.loads(row['payload'])) != payload:
                raise ValueError('This send request belongs to a different prompt or recipient list. Edit the draft to start a new send.')
        else:
            prepare_send(body, request_id)
        return send(request_id)


def send(approval_id, sender=None):
    sender = sender or tool
    if not isinstance(approval_id, str):
        raise ValueError('Invalid send request.')
    with SEND_LOCK, store.database() as db:
        db.execute('BEGIN IMMEDIATE')
        history.initialize(db)
        db.commit()
        row = db.execute('SELECT * FROM approvals WHERE id=?', (approval_id,)).fetchone()
        if not row or time.time() - row['created'] > 1800:
            raise ValueError('This send request expired. Edit the draft to start a new send.')
        payload = json.loads(row['payload'])
        results = []
        recorded = False
        history_error = None
        for target in payload['targets']:
            old = db.execute('SELECT state,receipt FROM deliveries WHERE approval=? AND thread=?',
                             (approval_id, target['threadId'])).fetchone()
            if old:
                results.append({'threadId': target['threadId'], 'projectId': target['projectId'], 'title': target['title'], 'state': old['state'],
                                'receipt': json.loads(old['receipt']) if old['receipt'] else None})
                continue
            db.execute('INSERT INTO deliveries VALUES (?, ?, ?, ?)', (approval_id, target['threadId'], 'unknown', None))
            db.commit()  # Record before transport. Never silently replay an uncertain write.
            try:
                if target['projectId'].startswith(browser_chats.PREFIX):
                    receipt = browser_chats.send(target, payload['english'])
                    if receipt.get('delivery') != 'browser_submit':
                        raise ValueError('Browser submission was not confirmed. Check the conversation before sending again.')
                    state = 'submitted'
                elif target['projectId'].startswith('desktop:'):
                    if os.environ.get('ARABIC_RELAY_NATIVE') != '1':
                        raise ValueError('Desktop chat sending requires the native Arabic Relay application.')
                    desktop_chats.prepare(target)
                    receipt = desktop_chats.send(target, payload['english'])
                    if receipt.get('delivery') != 'desktop_submit':
                        raise ValueError('Desktop submission was not confirmed. Check the app before sending again.')
                    state = 'submitted'
                else:
                    receipt = sender('t3_thread_send', {'threadId': target['threadId'], 'message': payload['english'],
                                                      'mode': 'queue', 'clientRequestId': 'relay:' + approval_id + ':' + target['threadId']})
                    if receipt.get('delivery') not in ('started', 'queued', 'steered', 'restarted') or not receipt.get('messageId'):
                        raise ValueError('T3 did not confirm delivery. Check the target session before sending again.')
                    state = 'sent'
            except Exception as error:
                receipt = {'error': str(error)[:1500]}
                state = 'unknown'
            db.execute('UPDATE deliveries SET state=?,receipt=? WHERE approval=? AND thread=?',
                       (state, json.dumps(receipt), approval_id, target['threadId']))
            db.commit()
            if state in ('sent', 'submitted') and not recorded:
                # Existing receipts above never recreate a deleted history entry on a retry.
                recorded = True
                try:
                    history.record(db, approval_id, payload)
                    db.commit()
                except sqlite3.Error:
                    db.rollback()
                    history_error = 'Submission was accepted, but history could not be saved. Do not resend just to save history.'
            results.append({'threadId': target['threadId'], 'projectId': target['projectId'], 'title': target['title'], 'state': state, 'receipt': receipt})
        return {'deliveries': results, **({'historyError': history_error} if history_error else {})}
