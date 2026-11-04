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

    def short_recording(self):
        self.audio = self.recognizer.audio = scripted_audio(9)

    def test_a_short_recording_is_read_again_whole_at_stop(self):
        self.short_recording()
        job = 'inline-' + 'h' * 30
        last = self.record(job, inline=True)
        whole = []

        def request(key, header, payload, timeout=300, cancel_event=None):
            whole.append((key, header, payload))
            return {'text': 'مش عايز أغيره the schema', 'device': 'GPU', 'segments': [], 'seconds': 9.0}
        with patch.object(speech, 'engine_request', request):
            status, body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['text'], 'مش عايز أغيره the schema')
        self.assertEqual(whole, [(('model', 'cuda'), {'format': 'pcm16', 'keywords': [], 'inline': True}, self.audio)])
        self.assertEqual((body['incremental']['used'], body['incremental']['finalDecode'], body['incremental']['fallbackReason']),
                         (True, 'whole', None))
        self.assertEqual(body['timings']['path'], 'incremental-whole')
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def finish_with_reading(self, job, last, segments):
        reading = {'text': 'x', 'device': 'GPU', 'segments': segments, 'seconds': 9.0}
        with patch.object(speech, 'engine_request', return_value=reading):
            return self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})[1]

    def test_words_recognition_doubted_in_a_row_are_named(self):
        self.short_recording()
        job = 'inline-' + 'l' * 30
        last = self.record(job, inline=True)
        # Measured on real clips: "مش" (0.05) and "slash" (0.04) were right, and "Why do I" was the real mishearing.
        heard = [(' مش', .05), (' عايز', .82), (' Why', .34), (' do', .37), (' I', .47), (' want', .7), (' slash', .04), (' it', None), (' and', .24)]
        segments = [{'text': '', 'avg_logprob': -.2, 'compression_ratio': 1.0,
                     'words': [{'text': text, 'probability': chance} for text, chance in heard]}]
        body = self.finish_with_reading(job, last, segments)
        self.assertEqual(body['doubtfulWords'], ['Why do I'])
        self.assertIs(body['hardToHear'], False)

    def test_a_recording_hard_to_hear_as_a_whole_is_reported(self):
        # Words dropped from speech leave no doubtful word behind; the whole segment's low score is the only sign.
        self.short_recording()
        job = 'inline-' + 'm' * 30
        last = self.record(job, inline=True)
        body = self.finish_with_reading(job, last, [{'text': '', 'avg_logprob': -.54, 'compression_ratio': .96, 'words': []}])
        self.assertEqual(body['doubtfulWords'], [])
        self.assertIs(body['hardToHear'], True)
        self.assertEqual(body['issues'], [])                      # it warns, and never blocks pasting

    def test_a_clear_recording_raises_no_doubt(self):
        self.short_recording()
        job = 'inline-' + 'o' * 30
        last = self.record(job, inline=True)
        body = self.finish_with_reading(job, last, [{'text': '', 'avg_logprob': -.19, 'compression_ratio': 1.04,
                                                     'words': [{'text': ' fine', 'probability': .99}]}])
        self.assertEqual((body['doubtfulWords'], body['hardToHear']), ([], False))

    def test_an_inline_recording_loads_the_local_translator_and_a_craft_one_does_not(self):
        with patch.object(speech.local_translator, 'warm') as warm:
            self.post('/api/stream/start', {'jobId': 'main-' + 'p' * 30})
            warm.assert_not_called()
            self.post('/api/stream/cancel', {'jobId': 'main-' + 'p' * 30})
            self.post('/api/stream/start', {'jobId': 'inline-' + 'q' * 30, 'inline': True})
            warm.assert_called_once_with()

    def test_quitting_frees_the_local_translator(self):
        with patch.object(speech.local_translator, 'unload') as unload, patch.object(speech.engine_host.HOST, 'stop'):
            speech.shutdown_engine()
        unload.assert_called_once_with()

    def test_the_local_translator_setting_round_trips(self):
        with patch.object(speech.local_translator, 'running', return_value=False):
            connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=10)
            connection.request('GET', '/api/local-translator', headers={'Origin': server.ORIGIN})
            self.assertEqual(json.loads(connection.getresponse().read()), {'enabled': True, 'installed': False, 'model': 'gemma4:e4b'})
            connection.close()
            self.assertEqual(self.post('/api/local-translator', {'enabled': False})[1]['enabled'], False)
            self.assertEqual(self.post('/api/local-translator', {'enabled': 'no'})[0], 400)

    def test_a_failed_whole_reading_is_reported_and_releases_the_recording(self):
        self.short_recording()
        job = 'main-' + 'j' * 30
        last = self.record(job)
        with patch.object(speech, 'engine_request', side_effect=ValueError('Local speech failed on this recording.')):
            status, body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})
        self.assertEqual(status, 400, body)                       # the page then sends its full recording instead
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_on_the_cpu_a_short_recording_keeps_the_reading_from_its_passes(self):
        self.short_recording()
        job = 'main-' + 'k' * 30
        with patch.object(speech, 'speech_device', lambda: 'cpu'), patch.object(speech, 'engine_request', side_effect=AssertionError('no second reading')):
            last = self.record(job)
            status, body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['timings']['path'], 'incremental')

    def test_a_worker_that_moved_to_the_cpu_keeps_the_reading_from_its_passes(self):
        self.short_recording()
        heard = self.recognizer

        def on_cpu(header, payload, cancel_event):
            return {**heard(header, payload, cancel_event), 'device': 'CPU'}
        job = 'main-' + 'n' * 30
        with patch.object(speech, 'window_transcriber', lambda key: on_cpu), \
                patch.object(speech, 'engine_request', side_effect=AssertionError('no second reading')):
            last = self.record(job)
            status, body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['timings']['path'], 'incremental')

    def test_a_long_recording_keeps_the_reading_from_its_passes_and_lock_released(self):
        job = 'main-' + 'a' * 30
        last = self.record(job)
        with patch.object(speech, 'engine_request', side_effect=AssertionError('no second reading')):
            status, body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['text'].split(), [word for word, _ in SCRIPT])
        self.assertEqual(body['incremental']['used'], True)
        self.assertEqual(body['timings']['path'], 'incremental')
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_approved_rules_are_the_ones_in_force_when_the_recording_started(self):
        rule = speech.aliases.add('سلام', 'Salam')['aliases'][0]['id']
        job = 'main-' + 'r' * 30
        last = self.record(job)
        # Rules edited while this recording is underway apply to the next recording, never halfway through one.
        speech.aliases.update(rule, enabled=False)
        speech.aliases.add('خالص', 'totally')
        status, body = self.post('/api/stream/finish', {'jobId': job, 'lastSeq': last, 'totalSamples': len(self.audio) // 2})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['text'].split()[0], 'Salam')
        self.assertEqual(body['text'].split()[-1], 'خالص')
        self.assertEqual(body['rawText'].split()[0], 'سلام')

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
        self.assertEqual(body['incremental'], {'used': False, 'passes': 0, 'finalDecode': 'whole', 'tailSeconds': None, 'fallbackReason': 'failed'})
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
