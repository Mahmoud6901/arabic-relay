"""Every API route the interface calls must reach a handler in server.py."""
import http.client
import json
import io
from pathlib import Path
import re
import tempfile
import threading
import tokenize
import unittest
from unittest.mock import Mock, patch
from arabic_relay import __version__, models, paths, server, translators


class Routes(unittest.TestCase):
    def setUp(self):
        # POSTing every route must never touch real data or the real T3 service.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for target, name, value in ((paths, 'DATA', Path(temp.name)),
                                    (server.t3, 'request_json', Mock(side_effect=ValueError('no T3 in tests'))),
                                    (server.direct_sessions, 'connect_claude', Mock(return_value={})),
                                    (server.direct_sessions, 'connect_codex', Mock(return_value={})),
                                    (models, 'gpu_info', Mock(return_value=None)),
                                    (translators, 'catalog', Mock(return_value={'codex': {'models': []}, 'claude': {'models': []}}))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.port = self.httpd.server_address[1]
        origin = patch.object(server, 'ORIGIN', f'http://127.0.0.1:{self.port}')
        origin.start()
        self.addCleanup(origin.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def status(self, method, url):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Origin': server.ORIGIN, 'X-Relay-Token': server.CSRF, 'Content-Type': 'application/json'}
        # An empty JSON object reaches the route; validation errors (400) prove routing happened.
        connection.request(method, url, body=b'{}' if method == 'POST' else None, headers=headers)
        code = connection.getresponse().status
        connection.close()
        return code

    def test_every_interface_route_is_handled(self):
        html = (paths.ROOT / 'index.html').read_text(encoding='utf-8')
        routes = sorted(set(re.findall(r"""['"`](/api/[a-z/-]+)""", html)))
        self.assertIn('/api/catalog', routes)
        self.assertIn('/api/transcribe', routes)
        unhandled = []
        with patch.object(server.t3, 'load_auth', return_value=None), \
                patch.object(server.direct_sessions, 'catalog', return_value={'projects': [], 'apps': []}), \
                patch.object(server.translate, 'codex_text', side_effect=ValueError('no network in tests')), \
                patch.object(server.speech, 'open_wispr_flow', return_value={'installed': False}):
            for route in routes:
                if self.status('GET', route) == 404 and self.status('POST', route) == 404:
                    unhandled.append(route)
        self.assertEqual(unhandled, [])

    def test_status_reports_prompt_choice(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        connection.request('GET', '/api/status', headers={'Origin': server.ORIGIN})
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 200, body)
        self.assertEqual(set(body['promptChoice']), {'tool', 'model', 'effort'})
        self.assertIn(' · ', body['promptLabel'])

    def test_direct_setup_routes_require_same_origin_and_token(self):
        for provider in ('claude', 'codex'):
            route = '/api/direct/' + provider + '/connect'
            self.assertEqual(self.status('POST', route), 200)
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
            connection.request('POST', route, body=b'{}', headers={'Origin': 'https://example.com'})
            self.assertEqual(connection.getresponse().status, 403)
            connection.close()

    def test_browser_companion_routes_are_removed(self):
        for route in ('/api/browser/pair', '/api/browser/disconnect', '/bridge/pair', '/bridge/sync', '/bridge/result'):
            self.assertEqual(self.status('POST', route), 404)
        self.assertEqual(self.status('GET', '/api/browser/catalog'), 404)

    def test_translator_routes_are_handled(self):
        for method, route in (('GET', '/api/translators'), ('POST', '/api/prompt-choice'), ('POST', '/api/tool-path')):
            self.assertNotEqual(self.status(method, route), 404, route)
        self.assertEqual(self.status('POST', '/api/prompt-model'), 404)  # replaced by /api/prompt-choice

    def test_translators_route_copies_the_cache(self):
        cached = {'codex': {'models': []}, 'claude': {'models': []}}
        with patch.object(translators, 'catalog', return_value=cached):
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
            connection.request('GET', '/api/translators', headers={'Origin': server.ORIGIN})
            body = json.loads(connection.getresponse().read())
            connection.close()
        self.assertEqual(set(body['choice']), {'tool', 'model', 'effort'})
        self.assertIn(' · ', body['label'])
        self.assertNotIn('choice', cached)  # the shared catalog cache is never changed by a response

    def test_status_reports_missing_tools(self):
        with patch.object(server.locate, 'missing_tools', return_value={'codex': 'C:\\Tools\\codex.exe'}):
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
            connection.request('GET', '/api/status', headers={'Origin': server.ORIGIN})
            body = json.loads(connection.getresponse().read())
            connection.close()
        self.assertEqual(body['missingTools'], {'codex': 'C:\\Tools\\codex.exe'})

    def test_diagnostics_routes_are_handled(self):
        with patch.object(server.diagnostics, 'report', return_value='report'):
            for route in ('/api/diagnostics', '/api/crash/dismiss'):
                self.assertNotEqual(self.status('POST', route), 404, route)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        connection.request('GET', '/api/status', headers={'Origin': server.ORIGIN})
        body = json.loads(connection.getresponse().read())
        connection.close()
        self.assertEqual(body['version'], __version__)
        self.assertIs(body['crashed'], False)

    def test_update_routes_are_handled(self):
        for route in ('/api/update/install', '/api/update/preferences'):
            self.assertNotEqual(self.status('POST', route), 404, route)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        connection.request('GET', '/api/status', headers={'Origin': server.ORIGIN})
        body = json.loads(connection.getresponse().read())
        connection.close()
        self.assertEqual(set(body['update']), {'mode', 'installOnQuit', 'available', 'ready', 'failed', 'releaseUrl'})

    def test_setup_routes_are_handled(self):
        for method, route in (('GET', '/api/setup'), ('GET', '/api/setup/progress'), ('POST', '/api/setup/download'),
                              ('POST', '/api/setup/cancel'), ('POST', '/api/setup/skip'), ('POST', '/api/speech-device'),
                              ('POST', '/api/model/delete')):
            self.assertNotEqual(self.status(method, route), 404, route)


class Messages(unittest.TestCase):
    def test_no_module_names_inside_strings(self):
        # Splitting app.py must not leak "t3.T3" or "/api/t3.send" into routes or user-facing text.
        qualified = re.compile(r'\b(locate|paths|server|speech|store|t3|translate)\.(?!bin\b|tmp\b)[A-Za-z_]+')
        leaks = []
        for source in sorted(paths.ROOT.glob('*.py')):
            tokens = tokenize.generate_tokens(io.StringIO(source.read_text(encoding='utf-8')).readline)
            leaks += [f'{source.name}:{t.start[0]}: {t.string[:80]}' for t in tokens
                      if t.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE) and not t.string.startswith('"""')
                      and qualified.search(t.string)]
        self.assertEqual(leaks, [])


if __name__ == '__main__':
    unittest.main()
