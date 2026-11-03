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

from . import __version__, aliases, direct_sessions, diagnostics, firstrun, history, locate, paths, speech, store, t3, translate, translators, updates
from . import inline

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
        route = urllib.parse.urlparse(self.path).path
        if getattr(self, 'started', None) is not None and route in (
                '/api/status', '/api/catalog', '/api/direct/catalog', '/api/transcribe',
                '/api/translate', '/api/inline', '/api/restore-english', '/api/speech/prepare', '/api/stream/finish'):
            elapsed = round((time.monotonic() - self.started) * 1000)
            if elapsed >= 1000 or route in ('/api/transcribe', '/api/translate', '/api/inline', '/api/stream/finish'):
                # Only the fixed route, duration and HTTP status; never content, audio, IDs or tokens.
                logging.info('Request timing: %s %d ms (HTTP %d)', route, elapsed, status)
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
        self.started = time.monotonic()
        if not self.valid_host():
            return self.respond({'error': 'Invalid host'}, 403)
        path = urllib.parse.urlparse(self.path)
        try:
            if path.path == '/':
                return self.respond((ROOT / 'index.html').read_bytes(), content_type='text/html; charset=utf-8')
            if path.path == '/icon.svg':
                return self.respond((ROOT / 'assets/icon.svg').read_bytes(), content_type='image/svg+xml')
            if path.path == '/pcm-capture.js':
                return self.respond((ROOT / 'assets/pcm-capture.js').read_bytes(), content_type='text/javascript; charset=utf-8')
            if path.path in ('/fonts/Geist.ttf', '/fonts/InstrumentSerif-Italic.ttf', '/fonts/InstrumentSerif-Regular.ttf', '/fonts/DMMono-Regular.ttf', '/fonts/NotoSansArabic.ttf'):
                return self.respond((ROOT / path.path.lstrip('/')).read_bytes(), content_type='font/ttf')
            if path.path == '/api/status':
                auth = None
                return self.respond({'app': 'arabic-relay', 'version': __version__, 'crashed': diagnostics.crashed(), 'update': updates.state(),
                                     'connected': False, 'csrf': CSRF,
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
            if path.path == '/api/aliases':
                return self.respond(aliases.listing())
            return self.respond({'error': 'Not found'}, 404)
        except Exception as error:
            self.fail(error)

    def fail(self, error):
        if not isinstance(error, ValueError):  # ValueError carries user-facing copy; anything else is a bug
            logging.exception('%s %s failed', self.command, urllib.parse.urlparse(self.path).path)
        self.respond({'error': str(error)}, 400)

    def do_POST(self):
        self.started = time.monotonic()
        if not self.valid_host() or self.headers.get('Origin') != ORIGIN or self.headers.get('X-Relay-Token') != CSRF:
            return self.respond({'error': 'Request must originate from this Relay page.'}, 403)
        try:
            size = int(self.headers.get('Content-Length', 0))
            if self.path == '/api/transcribe':
                if not 0 < size <= speech.MAX_AUDIO:
                    raise ValueError('Keep each recording under 24 MB.')
                vocabulary = urllib.parse.unquote(self.headers.get('X-Relay-Vocabulary', ''))
                options = {}
                if self.headers.get('X-Relay-Inline') == '1':
                    options = {'inline_mode': True, 'retry': self.headers.get('X-Relay-Retry') == '1',
                               'retry_seconds': float(self.headers.get('X-Relay-Retry-Seconds', '30'))}
                    rid = self.headers.get('X-Relay-Inline-Request')
                    if rid:
                        if not isinstance(rid, str) or len(rid) > 100:
                            raise ValueError('Invalid transcription request.')
                        options['cancel_event'] = inline.job(rid)
                return self.respond(speech.transcribe(self.rfile.read(size), self.headers.get('Content-Type', ''), vocabulary,
                                               self.headers.get('X-Relay-Provider', 'whisper'), **options))
            if self.path == '/api/stream/chunk':
                # Raw 16 kHz mono PCM; the server enforces the limits, not only the page.
                if not 0 < size <= speech.incremental.MAX_CHUNK * 2 or self.headers.get('Content-Type') != 'application/octet-stream':
                    raise ValueError('Invalid recording audio.')
                try:
                    seq = int(self.headers.get('X-Relay-Stream-Seq', ''))
                except ValueError:
                    raise ValueError('Invalid recording audio.') from None
                return self.respond(speech.stream_chunk(self.headers.get('X-Relay-Stream-Job'), seq, self.rfile.read(size)))
            if not 0 < size <= 100000:
                raise ValueError('Invalid request size.')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Invalid request.')
            if self.path == '/api/speech/prepare':
                return self.respond(speech.prepare_worker(body.get('requestId')))
            if self.path == '/api/speech/cancel-prepare':
                if not isinstance(body.get('requestId'), str):
                    raise ValueError('Invalid recording request.')
                return self.respond(speech.cancel_preparation(body['requestId']))
            if self.path == '/api/stream/start':
                return self.respond(speech.stream_start(body.get('jobId'), body.get('inline', False), body.get('vocabulary', '')))
            if self.path == '/api/stream/finish':
                return self.respond(speech.stream_finish(body.get('jobId'), body.get('lastSeq'), body.get('totalSamples')))
            if self.path == '/api/stream/cancel':
                return self.respond(speech.stream_cancel(body.get('jobId')))
            if self.path == '/api/incremental':
                return self.respond(speech.set_incremental(body.get('enabled')))
            if self.path == '/api/engine':
                return self.respond(speech.set_engine_settings(body.get('idleSeconds'), body.get('prewarm'),
                                                               body.get('retryGpu') is True))
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
            if self.path == '/api/restore-english':
                return self.respond(translate.restore_english(body.get('text'), body.get('vocabulary', '')))
            if self.path == '/api/translate':
                return self.respond(translate.translate(body.get('arabic'), body.get('original', '')))
            if self.path == '/api/inline':
                return self.respond(inline.process(body))
            if self.path == '/api/inline/cancel':
                return self.respond(inline.cancel(body.get('requestId')))
            if self.path == '/api/inline/validate':
                return self.respond(inline.manual(body))
            if self.path == '/api/aliases/add':
                return self.respond(aliases.add(body.get('heard'), body.get('canonical'), body.get('provenance', 'user')))
            if self.path == '/api/aliases/update':
                return self.respond(aliases.update(body.get('id'), body.get('heard'), body.get('canonical'), body.get('enabled')))
            if self.path == '/api/aliases/delete':
                return self.respond(aliases.delete(body.get('id')))
            if self.path == '/api/aliases/apply':
                return self.respond(aliases.apply(body.get('text')))
            if self.path == '/api/forget-vocabulary':
                return self.respond(store.forget_vocabulary())
            if self.path == '/api/self-learning':
                return self.respond(store.set_self_learning(body.get('enabled')))
            if self.path == '/api/auto-spelling':
                return self.respond(store.set_auto_spelling(body.get('enabled')))
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
