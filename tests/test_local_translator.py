"""The local translator against a stand-in Ollama server: what it asks for, what it accepts, and how it fails."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from arabic_relay import inline, local_translator, paths, store


class StandIn:
    """A real HTTP server on a free port that answers the way Ollama does."""

    def __init__(self, test):
        self.chat, self.chat_status, self.tags, self.calls, self.delay = {}, 200, {'models': []}, [], 0
        self.pull = []
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, data):
                raw = json.dumps(data).encode()
                try:
                    self.send_response(status)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass                     # the client stopped waiting

            def do_GET(self):
                self.reply(200, stand_in.tags)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                stand_in.calls.append((self.path, body))
                if self.path == '/api/pull':
                    raw = b''.join(json.dumps(row).encode() + b'\n' for row in stand_in.pull)
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                time.sleep(stand_in.delay)
                self.reply(stand_in.chat_status, stand_in.chat)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        test.addCleanup(self.httpd.server_close)
        test.addCleanup(self.httpd.shutdown)
        url = patch.object(local_translator, 'URL', f'http://127.0.0.1:{self.httpd.server_address[1]}')
        url.start()
        test.addCleanup(url.stop)

    def answers(self, content, **extra):
        self.chat = {'message': {'role': 'assistant', 'content': content if isinstance(content, str) else json.dumps(content)},
                     'done_reason': 'stop', **extra}


def request(text, **extra):
    return inline.request({'text': text, 'requestId': 'local-test-request-1234567', 'revision': 3, **extra})[0]


class TranslatorDownload(unittest.TestCase):
    def setUp(self):
        self.ollama = StandIn(self)
        self.job = local_translator.PullJob()

    def finished(self):
        deadline = time.monotonic() + 5
        while self.job.progress()['state'] == 'downloading' and time.monotonic() < deadline:
            time.sleep(.01)
        return self.job.progress()

    def test_adds_up_every_layer_and_finishes(self):
        self.ollama.pull = [{'status': 'pulling manifest'},
                            {'status': 'pulling a', 'digest': 'a', 'total': 100, 'completed': 40},
                            {'status': 'pulling b', 'digest': 'b', 'total': 50, 'completed': 5},
                            {'status': 'pulling a', 'digest': 'a', 'total': 100, 'completed': 100},
                            {'status': 'pulling b', 'digest': 'b', 'total': 50, 'completed': 50},
                            {'status': 'success'}]
        self.assertEqual(self.job.start()['state'], 'downloading')
        self.assertEqual(self.finished(), {'state': 'done', 'done': 150, 'total': 150, 'error': None})
        self.assertEqual(self.ollama.calls[-1], ('/api/pull', {'model': local_translator.MODEL, 'stream': True}))

    def test_an_error_from_ollama_is_reported_without_its_text(self):
        self.ollama.pull = [{'error': 'pull model manifest: file does not exist'}]
        self.job.start()
        result = self.finished()
        self.assertEqual((result['state'], result['error']), ('error', local_translator.PULL_FAILED))

    def test_a_stream_that_ends_early_is_a_failure(self):
        self.ollama.pull = [{'status': 'pulling a', 'digest': 'a', 'total': 100, 'completed': 40}]
        self.job.start()
        self.assertEqual(self.finished()['state'], 'error')

    def test_refuses_when_ollama_is_not_running(self):
        with patch.object(local_translator, 'URL', 'http://127.0.0.1:9'):
            with self.assertRaisesRegex(ValueError, local_translator.NOT_RUNNING):
                self.job.start()
        self.assertEqual(self.job.progress()['state'], 'idle')

    def test_one_download_at_a_time_and_a_cancel_stays_cancelled(self):
        self.job.cancelled = threading.Event()
        self.job.state = {'state': 'downloading', 'done': 0, 'total': 0, 'error': None}
        with self.assertRaises(ValueError):
            self.job.start()
        self.assertEqual(self.job.cancel()['state'], 'cancelled')
        self.job.finish(self.job.cancelled, state='done')     # the download thread reports late
        self.assertEqual(self.job.progress()['state'], 'cancelled')


class LocalTranslator(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        data = patch.object(paths, 'DATA', Path(temp.name))
        data.start()
        self.addCleanup(data.stop)
        self.ollama = StandIn(self)
        local_translator.unload()                            # an earlier test's use of the model does not carry over
        self.ollama.calls.clear()

    def test_only_the_arabic_phrases_change(self):
        req = request('Please راجع the login page and اتأكد من the error messages.')
        self.ollama.answers({'1': 'review', '2': 'make sure of'})
        output, _, issues = inline.reconstruct(req, local_translator.translate(req))
        self.assertEqual(issues, [])
        self.assertEqual(output, 'Please review the login page and make sure of the error messages.')

    def test_the_model_sees_the_marked_transcript_and_must_answer_every_phrase(self):
        req = request('Please راجع the login page and اتأكد من the error messages.')
        self.ollama.answers({'1': 'review', '2': 'make sure of'})
        local_translator.translate(req)
        path, body = self.ollama.calls[-1]
        self.assertEqual(path, '/api/chat')
        self.assertEqual(body['model'], 'gemma4:e4b')
        self.assertIs(body['think'], False)
        self.assertEqual(body['messages'][-1], {'role': 'user', 'content': 'Please [1: راجع] the login page and [2: اتأكد من] the error messages.'})
        self.assertEqual(body['format']['required'], ['1', '2'])

    def test_end_punctuation_the_source_did_not_have_is_dropped(self):
        req = request('What happened هنا?')
        self.ollama.answers({'1': 'here?'})
        self.assertEqual(inline.reconstruct(req, local_translator.translate(req))[0], 'What happened here?')

    def test_any_bad_answer_is_unavailable(self):
        req = request('Please راجع the page and اتأكد من it.')
        for content in ('not json', '[]', {'1': 'review'}, {'1': 'review', '2': 7}, {'1': 'review', '2': ''}, {'1': 'review', '2': '...'},
                        {'1': 'review', '2': 'check', '3': 'extra'}):
            with self.subTest(content=content):
                self.ollama.answers(content)
                with self.assertRaises(local_translator.Unavailable):
                    local_translator.translate(req)
        self.ollama.chat = {'done_reason': 'stop'}
        with self.assertRaises(local_translator.Unavailable):
            local_translator.translate(req)

    def test_an_answer_cut_off_at_its_length_limit_is_unavailable(self):
        req = request('Please راجع it.')
        self.ollama.answers({'1': 'review'}, done_reason='length')
        with self.assertRaises(local_translator.Unavailable):
            local_translator.translate(req)

    def test_a_missing_model_or_a_stopped_server_is_unavailable(self):
        req = request('Please راجع it.')
        self.ollama.chat_status, self.ollama.chat = 404, {'error': 'model not found'}
        with self.assertRaises(local_translator.Unavailable):
            local_translator.translate(req)
        with patch.object(local_translator, 'URL', 'http://127.0.0.1:9'):
            with self.assertRaises(local_translator.Unavailable):
                local_translator.translate(req)

    def test_a_stopped_server_is_noticed_at_once(self):
        # Windows takes about 2 s to refuse a closed port: too long to pay before every online translation.
        req = request('Please راجع it.')
        started = time.monotonic()
        with patch.object(local_translator, 'URL', 'http://127.0.0.1:9'):
            with self.assertRaises(local_translator.Unavailable):
                local_translator.translate(req)
        self.assertLess(time.monotonic() - started, 1)

    def test_a_slow_server_is_unavailable(self):
        req = request('Please راجع it.')
        self.ollama.answers({'1': 'review'})
        self.ollama.delay = .5
        with patch.object(local_translator, 'TIMEOUT', .1):
            with self.assertRaises(local_translator.Unavailable):
                local_translator.translate(req)

    def test_status_reports_whether_the_model_is_installed_and_wanted(self):
        self.assertEqual(local_translator.status(), {'enabled': True, 'installed': False, 'model': 'gemma4:e4b', 'running': True})
        self.ollama.tags = {'models': [{'name': 'gemma3:4b'}, {'name': 'gemma4:e4b'}]}
        self.assertTrue(local_translator.status()['installed'])
        self.assertEqual(local_translator.set_enabled(False)['enabled'], False)
        self.assertFalse(local_translator.enabled())
        with patch.object(local_translator, 'URL', 'http://127.0.0.1:9'):
            self.assertFalse(local_translator.status()['installed'])
        for value in (None, 0, 1, 'false'):
            with self.assertRaises(ValueError):
                local_translator.set_enabled(value)

    def test_the_model_stays_loaded_as_long_as_the_speech_model(self):
        store.set_setting('engine_idle_seconds', 900)
        req = request('Please راجع it.')
        self.ollama.answers({'1': 'review'})
        local_translator.translate(req)
        self.assertEqual(self.ollama.calls[-1][1]['keep_alive'], 900)
        store.set_setting('engine_idle_seconds', 0)          # "until I quit"
        local_translator.translate(req)
        self.assertEqual(self.ollama.calls[-1][1]['keep_alive'], -1)

    def test_warming_loads_the_model_in_the_background_without_translating(self):
        self.ollama.answers('')
        self.assertTrue(local_translator.warm().join(5) is None)
        path, body = self.ollama.calls[-1]
        self.assertEqual((path, body['model'], body['messages']), ('/api/chat', 'gemma4:e4b', []))

    def test_warming_is_skipped_when_turned_off_and_survives_a_stopped_server(self):
        local_translator.set_enabled(False)
        self.assertIsNone(local_translator.warm())
        self.assertEqual(self.ollama.calls, [])
        local_translator.set_enabled(True)
        with patch.object(local_translator, 'URL', 'http://127.0.0.1:9'):
            local_translator.warm().join(5)

    def test_unload_frees_the_model_only_if_this_app_used_it(self):
        local_translator.unload()
        self.assertEqual(self.ollama.calls, [])
        self.ollama.answers({'1': 'review'})
        local_translator.translate(request('Please راجع it.'))
        local_translator.unload()
        self.assertEqual((self.ollama.calls[-1][1]['keep_alive'], self.ollama.calls[-1][1]['messages']), (0, []))
        calls = len(self.ollama.calls)
        local_translator.unload()
        self.assertEqual(len(self.ollama.calls), calls)

    def test_a_stopped_server_does_not_hold_up_quitting(self):
        self.ollama.answers({'1': 'review'})
        local_translator.translate(request('Please راجع it.'))
        with patch.object(local_translator, 'URL', 'http://127.0.0.1:9'):
            local_translator.unload()

    def test_turning_it_off_frees_the_model(self):
        self.ollama.answers({'1': 'review'})
        local_translator.translate(request('Please راجع it.'))
        local_translator.set_enabled(False)
        self.assertEqual(self.ollama.calls[-1][1]['keep_alive'], 0)


if __name__ == '__main__':
    unittest.main()
