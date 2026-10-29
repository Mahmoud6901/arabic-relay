"""Arabic Relay: local prompt preparation and explicit one-click dispatch."""
import base64
import hashlib
import json
import logging
import os
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, direct_sessions, diagnostics, firstrun, history, locate, paths, speech, store, t3, translate, translators, updates

ROOT = paths.ROOT
PORT = int(os.environ.get('ARABIC_RELAY_PORT', '8788'))
ORIGIN = f'http://127.0.0.1:{PORT}'
CSRF = secrets.token_urlsafe(32)


def catalog_response():
    desktop = direct_sessions.catalog()
    snapshot = paths.DATA / 'catalog.json'
    t3_error = None
    if t3.load_auth():
        try:
            live = t3.catalog()
            temporary = paths.DATA / 'catalog.tmp'
            temporary.write_text(json.dumps(live, ensure_ascii=False), encoding='utf-8')
            temporary.replace(snapshot)
            return {'projects': desktop['projects'] + live, 'live': True, 'desktopApps': desktop['apps']}
        except Exception as error:
            t3_error = str(error)[:500]  # Keep independent desktop destinations visible.
    saved = json.loads(snapshot.read_text(encoding='utf-8')) if snapshot.is_file() else []
    return {'projects': desktop['projects'] + saved, 'live': False, 'desktopApps': desktop['apps'], 't3Error': t3_error}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, data, status=200, content_type='application/json; charset=utf-8'):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        return self.headers.get('Host') == urllib.parse.urlparse(ORIGIN).netloc

    def do_GET(self):
        if not self.valid_host():
            return self.respond({'error': 'Invalid host'}, 403)
        path = urllib.parse.urlparse(self.path)
        try:
            if path.path == '/':
                return self.respond((ROOT / 'index.html').read_bytes(), content_type='text/html; charset=utf-8')
            if path.path == '/icon.svg':
                return self.respond((ROOT / 'assets/icon.svg').read_bytes(), content_type='image/svg+xml')
            if path.path in ('/fonts/Geist.ttf', '/fonts/InstrumentSerif-Italic.ttf', '/fonts/InstrumentSerif-Regular.ttf', '/fonts/DMMono-Regular.ttf', '/fonts/NotoSansArabic.ttf'):
                return self.respond((ROOT / path.path.lstrip('/')).read_bytes(), content_type='font/ttf')
            if path.path == '/api/status':
                auth = t3.load_auth()
                return self.respond({'app': 'arabic-relay', 'version': __version__, 'crashed': diagnostics.crashed(), 'update': updates.state(),
                                     'connected': bool(auth), 'csrf': CSRF,
                                     'nativeDesktop': os.environ.get('ARABIC_RELAY_NATIVE') == '1',
                                     **speech.status(), 'setupNeeded': firstrun.setup_needed(),
                                     'wisprFlowInstalled': locate.tool_path('wispr') is not None,
                                     'cohereConfigured': (paths.DATA / 'cohere.bin').is_file(),
                                     'selfLearning': store.self_learning_enabled(),
                                     'autoSpelling': store.auto_spelling_enabled(),
                                     'promptChoice': (choice := translate.prompt_choice()),
                                     'promptLabel': translators.label(choice), 'missingTools': locate.missing_tools(),
                                     'translator': bool(locate.tool_path('codex') or locate.tool_path('claude'))})
            if path.path == '/api/history':
                return self.respond(history.entries())
            if path.path == '/api/translators':
                choice = translate.prompt_choice()  # copy: the catalog dict is the shared cache
                return self.respond({**translators.catalog(refresh=True), 'choice': choice, 'label': translators.label(choice)})
            if path.path == '/api/setup':
                return self.respond(firstrun.checklist())
            if path.path == '/api/setup/progress':
                return self.respond(firstrun.JOB.progress())
            if path.path == '/api/vocabulary':
                return self.respond(store.personal_vocabulary())
            if path.path == '/api/catalog':
                return self.respond(catalog_response())
            if path.path == '/api/direct/catalog':
                return self.respond(direct_sessions.catalog())
            if path.path == '/oauth/callback':
                query = urllib.parse.parse_qs(path.query)
                state = query.get('state', [''])[0]
                with store.AUTH_LOCK:
                    pending = t3.OAUTH.pop(state, None)
                if not pending or time.time() - pending['created'] > 600:
                    raise ValueError('This sign-in request expired. Return to Relay and click Connect T3.')
                if query.get('iss', [''])[0] != t3.T3:
                    raise ValueError('Sign-in issuer did not match T3.')
                code = query.get('code', [''])[0]
                if not code:
                    raise ValueError('Sign-in was declined. Return to Relay to try again.')
                token, _ = t3.request_json(t3.T3 + '/oauth/mcp/token', {
                    'grant_type': 'authorization_code', 'code': code, 'client_id': pending['client_id'],
                    'redirect_uri': ORIGIN + '/oauth/callback', 'code_verifier': pending['verifier'],
                    'resource': t3.T3 + '/mcp'}, form=True)
                token['client_id'] = pending['client_id']
                token['expires_at'] = time.time() + token.get('expires_in', 3600)
                with store.AUTH_LOCK:
                    t3.save_auth(token)
                    t3.MCP_SESSION = None
                    t3.MCP_PROTOCOL = None
                html = b'<html><title>Relay connected</title><body style="font:20px system-ui;padding:60px"><h1>T3 connected.</h1><p>You can return to Arabic Relay and click Refresh.</p><a href="/">Return to Relay</a></body></html>'
                return self.respond(html, content_type='text/html; charset=utf-8')
            return self.respond({'error': 'Not found'}, 404)
        except Exception as error:
            self.fail(error)

    def fail(self, error):
        if not isinstance(error, ValueError):  # ValueError carries user-facing copy; anything else is a bug
            logging.exception('%s %s failed', self.command, urllib.parse.urlparse(self.path).path)
        self.respond({'error': str(error)}, 400)

    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin') != ORIGIN or self.headers.get('X-Relay-Token') != CSRF:
            return self.respond({'error': 'Request must originate from this Relay page.'}, 403)
        try:
            size = int(self.headers.get('Content-Length', 0))
            if self.path == '/api/transcribe':
                if not 0 < size <= speech.MAX_AUDIO:
                    raise ValueError('Keep each recording under 24 MB.')
                vocabulary = urllib.parse.unquote(self.headers.get('X-Relay-Vocabulary', ''))
                return self.respond(speech.transcribe(self.rfile.read(size), self.headers.get('Content-Type', ''), vocabulary,
                                               self.headers.get('X-Relay-Provider', 'whisper')))
            if not 0 < size <= 100000:
                raise ValueError('Invalid request size.')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Invalid request.')
            if self.path == '/api/direct/claude/connect':
                return self.respond(direct_sessions.connect_claude())
            if self.path == '/api/direct/codex/connect':
                return self.respond(direct_sessions.connect_codex())
            if self.path == '/api/wispr/open':
                return self.respond(speech.open_wispr_flow())
            if self.path == '/api/whisper-model':
                return self.respond(speech.set_whisper_model(body.get('path')))
            if self.path == '/api/prompt-choice':
                return self.respond(translate.set_prompt_choice(body.get('tool'), body.get('model'), body.get('effort')))
            if self.path == '/api/tool-path':
                return self.respond(locate.set_tool_path(body.get('kind'), body.get('path')))
            if self.path == '/api/setup/download':
                return self.respond(firstrun.JOB.start(body.get('model'), body.get('fresh') is True))
            if self.path == '/api/setup/cancel':
                return self.respond(firstrun.JOB.cancel())
            if self.path == '/api/setup/skip':
                return self.respond(firstrun.skip())
            if self.path == '/api/speech-device':
                return self.respond(speech.set_cpu_only(body.get('cpuOnly')))
            if self.path == '/api/model/delete':
                return self.respond(speech.delete_current_model())
            if self.path == '/api/update/install':
                return self.respond(updates.install(True))
            if self.path == '/api/update/preferences':
                return self.respond(updates.set_preferences(body.get('mode'), body.get('installOnQuit')))
            if self.path == '/api/diagnostics':
                return self.respond({'text': diagnostics.report()})
            if self.path == '/api/crash/dismiss':
                return self.respond(diagnostics.dismiss_crash())
            if self.path == '/api/cohere/key':
                return self.respond(speech.save_cohere_key(body.get('key')))
            if self.path == '/api/connect':
                registered, _ = t3.request_json(t3.T3 + '/oauth/mcp/register', {
                    'client_name': 'Arabic Relay', 'redirect_uris': [ORIGIN + '/oauth/callback'],
                    'grant_types': ['authorization_code'], 'response_types': ['code'], 'token_endpoint_auth_method': 'none'})
                verifier = secrets.token_urlsafe(64)
                state = secrets.token_urlsafe(32)
                with store.AUTH_LOCK:
                    t3.OAUTH.clear()
                    t3.OAUTH[state] = {'client_id': registered['client_id'], 'verifier': verifier, 'created': time.time()}
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
                args = {'response_type': 'code', 'client_id': registered['client_id'], 'state': state,
                        'code_challenge': challenge, 'code_challenge_method': 'S256', 'redirect_uri': ORIGIN + '/oauth/callback',
                        'scope': 'orchestration:read orchestration:operate', 'resource': t3.T3 + '/mcp'}
                return self.respond({'url': t3.T3 + '/oauth/mcp/authorize?' + urllib.parse.urlencode(args)})
            if self.path == '/api/restore-english':
                return self.respond(translate.restore_english(body.get('text'), body.get('vocabulary', '')))
            if self.path == '/api/translate':
                return self.respond(translate.translate(body.get('arabic'), body.get('original', '')))
            if self.path == '/api/forget-vocabulary':
                return self.respond(store.forget_vocabulary())
            if self.path == '/api/self-learning':
                return self.respond(store.set_self_learning(body.get('enabled')))
            if self.path == '/api/auto-spelling':
                return self.respond(store.set_auto_spelling(body.get('enabled')))
            if self.path == '/api/send':
                return self.respond(t3.send_prompt(body))
            if self.path == '/api/history/delete':
                return self.respond(history.delete(body.get('id')))
            if self.path == '/api/history/clear':
                return self.respond(history.clear())
            return self.respond({'error': 'Not found'}, 404)
        except Exception as error:
            self.fail(error)


class Server(ThreadingHTTPServer):
    """No port sharing: Windows otherwise lets a second instance bind the same port."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def bind(port, fallback=True):
    """Serve on `port`, or on a free port when it is busy; PORT and ORIGIN follow the port actually bound."""
    global PORT, ORIGIN
    try:
        httpd = Server(('127.0.0.1', port), Handler)
    except OSError:
        if not fallback:
            raise
        logging.info('Port %s is busy; using a free port.', port)
        httpd = Server(('127.0.0.1', 0), Handler)
    PORT = httpd.server_address[1]
    ORIGIN = f'http://127.0.0.1:{PORT}'
    return httpd


def main(open_browser=False):
    with store.database():
        pass
    try:
        server = Server(('127.0.0.1', PORT), Handler)
    except OSError:
        if not open_browser:
            raise
        with urllib.request.urlopen(ORIGIN + '/api/status', timeout=3) as response:
            if 'csrf' not in json.load(response):
                raise ValueError(f'Port {PORT} is being used by another app.')
        webbrowser.open(ORIGIN)
        print('Arabic Relay is already running.', flush=True)
        sys.exit(0)
    print('Arabic Relay · Local: ' + ORIGIN, flush=True)
    if open_browser:
        webbrowser.open(ORIGIN)
    server.serve_forever()
