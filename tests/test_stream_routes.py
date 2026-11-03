"""Processing during speech through the real HTTP routes: ownership, limits, fallback and lock release."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from arabic_relay import paths, server, speech, store
from tests.test_incremental import RATE, SCRIPT, FakeRecognizer, scripted_audio


class StreamRoutes(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for target, name, value in ((paths, 'DATA', Path(temp.name)),
                                    (speech, 'speech_model_path', lambda: Path('model')),
                                    (speech, 'speech_device', lambda: 'cuda'),
                                    (speech.models, 'model_label', lambda folder: 'Whisper large-v3'),
                                    (speech.engine_host.HOST, 'ensure', lambda *a: {})):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        store.set_setting('incremental_dictation', 1)
        self.audio = scripted_audio()
        self.recognizer = FakeRecognizer(self.audio)
        patcher = patch.object(speech, 'window_transcriber', lambda key: self.recognizer)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        origin = patch.object(server, 'ORIGIN', f'http://127.0.0.1:{self.httpd.server_address[1]}')
        origin.start()
        self.addCleanup(origin.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(lambda: speech.incremental.REGISTRY.job and speech.release_stream(speech.incremental.REGISTRY.job))

    def post(self, route, body, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=30)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        connection.request('POST', route, body=raw, headers={'Origin': server.ORIGIN, 'X-Relay-Token': server.CSRF,
                                                             'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def chunk(self, job, seq, payload):
        return self.post('/api/stream/chunk', payload, {'Content-Type': 'application/octet-stream',
                                                         'X-Relay-Stream-Job': job, 'X-Relay-Stream-Seq': str(seq)})

    def record(self, job, inline=False):
        self.assertEqual(self.post('/api/stream/start', {'jobId': job, 'inline': inline})[1]['accepted'], True)
        seq = 0
        for start in range(0, len(self.audio), 2 * RATE):
            status, body = self.chunk(job, seq, self.audio[start:start + 2 * RATE])
            self.assertEqual(status, 200, body)
            seq += 1
        return seq - 1

    def test_final_transcript_from_windows_and_lock_released(self):
        job = 'main-' + 'a' * 30
        last = self.record(job)
        status, body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['text'].split(), [word for word, _ in SCRIPT])
        self.assertEqual(body['incremental']['used'], True)
        self.assertEqual(body['timings']['path'], 'incremental')
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_inline_receives_segments_with_word_times(self):
        job = 'inline-' + 'b' * 30
        last = self.record(job, inline=True)
        body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})[1]
        words = [w for s in body['segments'] for w in s['words']]
        self.assertEqual(len(words), len(SCRIPT))
        self.assertEqual(body['effectiveAsrConfig']['path'], 'incremental')
        self.assertTrue(all(call['inline'] for call in self.recognizer.calls))

    def test_another_surface_cannot_start_or_touch_an_active_recording(self):
        job = 'main-' + 'c' * 30
        self.assertTrue(self.post('/api/stream/start', {'jobId': job})[1]['accepted'])
        self.assertEqual(self.post('/api/stream/start', {'jobId': 'popup-' + 'd' * 30})[1], {'accepted': False, 'reason': 'busy'})
        status, body = self.chunk('popup-' + 'd' * 30, 0, b'\0\0' * 100)
        self.assertEqual(status, 400)
        status, body = self.post('/api/stream/finish', {'jobId': 'popup-' + 'd' * 30, 'lastSeq': 0, 'totalSamples': 0})
        self.assertEqual(status, 400)
        self.assertEqual(self.post('/api/stream/cancel', {'jobId': job})[1], {'cancelled': True})
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_bad_chunks_end_the_job(self):
        job = 'main-' + 'e' * 30
        self.post('/api/stream/start', {'jobId': job})
        self.assertEqual(self.chunk(job, 1, b'\0\0' * 100)[0], 400)   # out of order
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())
        self.assertEqual(self.chunk(job, 0, b'\0\0' * 100)[0], 400)   # the job no longer exists
        self.post('/api/stream/start', {'jobId': job + 'x'})
        status, _ = self.post('/api/stream/chunk', b'\0\0' * (2 * RATE + 1), {'Content-Type': 'application/octet-stream',
                                                                          'X-Relay-Stream-Job': job + 'x', 'X-Relay-Stream-Seq': '0'})
        self.assertEqual(status, 400)

    def test_uncertain_windows_fall_back_to_the_whole_recording(self):
        self.recognizer.fail_on = 1
        job = 'main-' + 'f' * 30
        last = self.record(job)
        whole = []
        def request(key, header, payload, timeout=300, cancel_event=None):
            whole.append((header, len(payload)))
            return {'text': 'whole recording', 'device': 'GPU'}
        with patch.object(speech, 'engine_request', request):
            body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})[1]
        self.assertEqual(body['text'], 'whole recording')
        self.assertEqual(body['incremental'], {'used': False, 'windows': 0, 'fallbackReason': 'failed'})
        self.assertEqual(whole, [({'format': 'pcm16', 'keywords': []}, len(self.audio))])
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_disabled_experiment_keeps_the_full_clip_path(self):
        store.set_setting('incremental_dictation', 0)
        self.assertEqual(self.post('/api/stream/start', {'jobId': 'main-' + 'g' * 30})[1], {'accepted': False, 'reason': 'disabled'})
        self.assertFalse(speech.engine_enabled())

    def test_the_capture_worklet_is_served_as_javascript(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=10)
        connection.request('GET', '/pcm-capture.js', headers={'Host': server.ORIGIN.split('//')[1]})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader('Content-Type').startswith('text/javascript'))
        self.assertIn(b"registerProcessor('relay-pcm-capture'", body)


if __name__ == '__main__':
    unittest.main()
