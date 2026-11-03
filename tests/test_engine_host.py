"""The reusable speech worker: framed protocol, real readiness, reuse, ownership, cancellation and recovery."""
import io
import json
from pathlib import Path
import struct
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from arabic_relay import engine_host, paths, speech, speech_worker

STUB = str(Path(__file__).parent / 'engine_stub.py')


def pcm(seconds=1):
    return (np.zeros(int(seconds * 16000), dtype='<i2')).tobytes()


class Framing(unittest.TestCase):
    def test_frames_round_trip_with_exact_payload_lengths(self):
        stream = io.BytesIO(speech_worker.frame({'v': 1, 'op': 'ping', 'id': 'a'}) +
                            speech_worker.frame({'v': 1, 'op': 'transcribe', 'id': 'b'}, b'\x01\x02\x03\x04'))
        first = speech_worker.read_frame(stream)
        second = speech_worker.read_frame(stream)
        self.assertEqual((first[0]['id'], first[1]), ('a', b''))
        self.assertEqual((second[0]['bytes'], second[1]), (4, b'\x01\x02\x03\x04'))
        self.assertIsNone(speech_worker.read_frame(stream))  # clean EOF ends the worker

    def test_bad_magic_oversized_header_and_payload_are_rejected(self):
        with self.assertRaises(ValueError):
            speech_worker.read_frame(io.BytesIO(b'XXXX' + struct.pack('>I', 2) + b'{}'))
        with self.assertRaises(ValueError):
            speech_worker.read_frame(io.BytesIO(b'RLY1' + struct.pack('>I', speech_worker.MAX_HEADER + 1)))
        header = json.dumps({'bytes': speech_worker.MAX_PAYLOAD + 1}).encode()
        with self.assertRaises(ValueError):
            speech_worker.read_frame(io.BytesIO(b'RLY1' + struct.pack('>I', len(header)) + header))

    def test_truncated_payload_is_not_a_request(self):
        data = speech_worker.frame({'v': 1, 'op': 'transcribe', 'id': 'x'}, b'\0' * 10)[:-3]
        self.assertIsNone(speech_worker.read_frame(io.BytesIO(data)))

    def test_window_and_payload_limits_are_enforced_in_the_worker(self):
        engine = Mock(transcribe=Mock(return_value={'text': 'x'}))
        with self.assertRaisesRegex(ValueError, 'INVALID_REQUEST'):
            speech_worker.handle_request(engine, {'format': 'pcm16', 'window': True}, pcm(17))
        with self.assertRaisesRegex(ValueError, 'INVALID_AUDIO'):
            speech_worker.handle_request(engine, {'format': 'pcm16'}, b'\0' * 3)
        with self.assertRaisesRegex(ValueError, 'INVALID_REQUEST'):
            speech_worker.handle_request(engine, {'format': 'mp3'}, b'\0\0')
        with self.assertRaisesRegex(ValueError, 'INVALID_REQUEST'):
            speech_worker.handle_request(engine, {'format': 'pcm16', 'keywords': ['x'] * 41}, pcm())
        engine.transcribe.assert_not_called()


class Host(unittest.TestCase):
    def host(self, behaviour='normal'):
        host = engine_host.EngineHost()
        patcher = patch.object(paths, 'self_command', lambda *args: [sys.executable, STUB, behaviour])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(host.stop)
        return host

    def test_ready_only_after_the_worker_acknowledges_a_warm_up(self):
        host = self.host()
        host.ensure('model', 'cpu')
        self.assertIn(host.status()['state'], ('starting', 'loading', 'loaded', 'warming', 'ready'))
        self.assertTrue(host.wait_ready(30))
        status = host.status()
        self.assertEqual((status['state'], status['loadMs'], status['warmMs'], status['loads']), ('ready', 5, 1, 1))

    def test_consecutive_requests_reuse_one_process(self):
        host = self.host()
        host.ensure('model', 'cpu')
        process = host.process
        first = host.request({'format': 'pcm16', 'keywords': ['React']}, pcm(1), timeout=30)
        host.ensure('model', 'cpu')
        second = host.request({'format': 'pcm16', 'keywords': [], 'window': True}, pcm(2), timeout=30)
        self.assertEqual(first['text'], '16000 samples React')
        self.assertEqual(second['text'], '32000 samples ')
        self.assertTrue(second['window'])
        self.assertIs(host.process, process)
        self.assertEqual((host.status()['loads'], host.status()['requests']), (1, 2))

    def test_a_model_switch_retires_the_old_worker_before_starting_another(self):
        host = self.host()
        host.ensure('old', 'cpu')
        host.wait_ready(30)
        old = host.process
        host.ensure('new', 'cpu')
        self.assertIsNotNone(old.poll())  # stopped before the new one exists: never two models at once
        self.assertEqual((host.key, host.status()['loads']), (('new', 'cpu'), 2))

    def test_failed_warm_up_never_reports_ready(self):
        host = self.host('fail-warm')
        host.ensure('model', 'cpu')
        with self.assertRaises(engine_host.EngineError) as caught:
            host.wait_ready(30)
        self.assertEqual(caught.exception.code, 'NOT_READY')
        self.assertEqual(host.status()['state'], 'failed')

    def test_a_crash_fails_the_request_and_reports_a_stopped_engine(self):
        host = self.host('crash')
        host.ensure('model', 'cpu')
        with self.assertRaises(engine_host.EngineError) as caught:
            host.request({'format': 'pcm16'}, pcm(), timeout=30)
        self.assertEqual(caught.exception.code, 'ENGINE_STOPPED')
        self.assertEqual(host.status()['state'], 'stopped')

    def test_cancellation_and_timeouts_kill_a_stalled_decode(self):
        host = self.host('slow')
        host.ensure('model', 'cpu')
        host.wait_ready(30)
        cancel = threading.Event()
        threading.Timer(.3, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(engine_host.EngineError) as caught:
            host.request({'format': 'pcm16'}, pcm(), timeout=30, cancel_event=cancel)
        self.assertEqual(caught.exception.code, 'CANCELLED')
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNone(host.process)
        host.ensure('model', 'cpu')
        with self.assertRaises(engine_host.EngineError) as caught:
            host.request({'format': 'pcm16'}, pcm(), timeout=1)
        self.assertEqual(caught.exception.code, 'TIMEOUT')

    def test_library_output_cannot_corrupt_the_protocol(self):
        host = self.host('noisy')
        host.ensure('model', 'cpu')
        self.assertEqual(host.request({'format': 'pcm16'}, pcm(), timeout=30)['text'], '16000 samples ')
        self.assertEqual(host.request({'format': 'pcm16'}, pcm(), timeout=30)['text'], '16000 samples ')

    def test_a_second_concurrent_request_reports_busy_without_queueing(self):
        host = self.host('slow')
        host.ensure('model', 'cpu')
        host.wait_ready(30)
        threading.Thread(target=lambda: self.assertRaises(engine_host.EngineError, host.request,
                                                          {'format': 'pcm16'}, pcm(), 3), daemon=True).start()
        time.sleep(.5)
        with self.assertRaises(engine_host.EngineError) as caught:
            host.request({'format': 'pcm16'}, pcm(), wait=False)
        self.assertEqual(caught.exception.code, 'BUSY')


class EngineRequests(unittest.TestCase):
    def test_a_gpu_crash_is_retried_once_on_the_cpu_and_disclosed(self):
        host = Mock()
        host.request.side_effect = [engine_host.EngineError('ENGINE_STOPPED'), {'text': 'نص', 'device': 'CPU'}]
        with patch.object(engine_host, 'HOST', host):
            result = speech.engine_request(('model', 'cuda'), {'format': 'encoded'}, b'audio')
        self.assertEqual([c.args for c in host.ensure.call_args_list], [('model', 'cuda'), ('model', 'cpu')])
        self.assertTrue(result['fallback'])

    def test_worker_codes_become_user_messages(self):
        host = Mock()
        host.request.side_effect = engine_host.EngineError('AUDIO_TOO_LONG')
        with patch.object(engine_host, 'HOST', host), self.assertRaisesRegex(ValueError, 'under five minutes'):
            speech.engine_request(('model', 'cpu'), {'format': 'encoded'}, b'audio')
        host.request.side_effect = engine_host.EngineError('CANCELLED')
        with patch.object(engine_host, 'HOST', host), self.assertRaisesRegex(ValueError, 'cancelled'):
            speech.engine_request(('model', 'cpu'), {'format': 'encoded'}, b'audio')


if __name__ == '__main__':
    unittest.main()
