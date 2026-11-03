"""Reusable worker lifecycle: idle release, holds, memory recycling, and bounded Auto-GPU failure memory."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from arabic_relay import engine_host, models, paths, speech, store
from tests.test_engine_host import STUB, pcm


class Idle(unittest.TestCase):
    def host(self):
        host = engine_host.EngineHost()
        patch.object(paths, 'self_command', lambda *args: [sys.executable, STUB, 'normal']).start()
        patch.object(engine_host, 'HOST', host).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(host.stop)
        return host

    def test_the_model_is_released_after_the_idle_limit_and_reloaded_on_demand(self):
        host = self.host()
        host.watch(1)
        host.ensure('model', 'cpu')
        host.wait_ready(30)
        process = host.process
        self.assertTrue(host.alive())
        deadline = time.monotonic() + 20
        while host.alive() and time.monotonic() < deadline:
            time.sleep(.5)
        self.assertFalse(host.alive(), 'idle model was not released')
        self.assertEqual(host.status()['state'], 'stopped')
        host.ensure('model', 'cpu')
        self.assertEqual(host.request({'format': 'pcm16'}, pcm(), timeout=30)['text'], '16000 samples ')
        self.assertIsNot(host.process, process)
        self.assertEqual(host.status()['loads'], 2)

    def test_a_recording_in_progress_keeps_the_model_loaded(self):
        host = self.host()
        host.watch(1)
        host.hold()
        host.ensure('model', 'cpu')
        host.wait_ready(30)
        time.sleep(5)
        self.assertTrue(host.alive(), 'an active recording must never lose the model')
        host.release_hold()
        deadline = time.monotonic() + 20
        while host.alive() and time.monotonic() < deadline:
            time.sleep(.5)
        self.assertFalse(host.alive())

    def test_zero_or_unset_keeps_the_model_until_the_app_quits(self):
        host = self.host()
        host.watch(0)
        host.ensure('model', 'cpu')
        host.wait_ready(30)
        time.sleep(3)
        self.assertTrue(host.alive())

    def test_memory_growth_recycles_the_worker(self):
        host = self.host()
        host.watch(1)
        host.ensure('model', 'cpu')
        host.wait_ready(30)
        host.requests = 5
        host.baseline_memory = 100
        host.info['memory'] = {'privateBytes': 140}
        self.assertAlmostEqual(host.growth(), .4, places=2)
        deadline = time.monotonic() + 20
        while host.alive() and time.monotonic() < deadline:
            time.sleep(.5)
        self.assertFalse(host.alive())


class DeviceMemory(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for target, name, value in ((paths, 'DATA', Path(temp.name)),
                                    (models, 'gpu_info', Mock(return_value={'name': 'NVIDIA RTX', 'vramGb': 12.0, 'driver': '1'})),
                                    (models, 'gpu_runtime_dir', Mock(return_value=Path('runtime')))):
            patch.object(target, name, value).start()
            self.addCleanup(patch.stopall)

    def test_auto_uses_the_gpu_then_suspends_it_after_a_device_failure(self):
        self.assertEqual(speech.speech_device(), 'cuda')
        speech.record_gpu_failure('device')
        self.assertEqual(speech.speech_device(), 'cpu')
        backoff = speech.engine_settings()['gpuBackoff']
        self.assertEqual((backoff['count'], backoff['driver']), (1, '1'))
        self.assertGreater(backoff['until'], time.time())
        speech.clear_gpu_backoff()
        self.assertEqual(speech.speech_device(), 'cuda')

    def test_a_transient_oom_or_a_different_driver_never_suspends_the_gpu(self):
        speech.record_gpu_failure('oom')
        self.assertEqual(speech.speech_device(), 'cuda')
        speech.record_gpu_failure('device')
        store.set_setting('gpu_backoff', json.dumps({'name': 'NVIDIA RTX', 'driver': '2', 'count': 1,
                                                     'until': int(time.time() + 600)}))
        self.assertEqual(speech.speech_device(), 'cuda', 'a new driver starts clean')
        store.set_setting('gpu_backoff', json.dumps({'name': 'NVIDIA RTX', 'driver': '1', 'count': 1,
                                                     'until': int(time.time() - 1)}))
        self.assertEqual(speech.speech_device(), 'cuda', 'an expired suspension does not persist')

    def test_explicit_cpu_selection_still_wins(self):
        speech.set_cpu_only(True)
        self.assertEqual(speech.speech_device(), 'cpu')
        speech.set_cpu_only(False)
        self.assertEqual(speech.speech_device(), 'cuda')

    def test_engine_settings_are_validated_and_saved(self):
        self.assertEqual(speech.engine_settings()['idleSeconds'], 300)
        with patch.object(engine_host.HOST, 'watch') as watch:
            speech.set_engine_settings(900, True)
        self.assertTrue(watch.called)
        self.assertEqual(speech.engine_settings(), {'idleSeconds': 900, 'prewarm': True, 'gpuBackoff': None})
        for value in (7, -1, '300', True):
            with self.assertRaises(ValueError):
                speech.set_engine_settings(value)

    def test_prewarm_only_runs_when_asked_and_needs_a_model(self):
        with patch.object(speech.threading, 'Thread') as thread, \
                patch.object(speech, 'speech_model_path', return_value=Path('model')):
            speech.prewarm()
        thread.assert_not_called()
        store.set_setting('engine_prewarm', 1)
        store.set_setting('incremental_dictation', 1)
        with patch.object(speech.threading, 'Thread') as thread, \
                patch.object(speech, 'speech_model_path', return_value=Path('model')):
            speech.prewarm()
        self.assertTrue(thread.called)
        with patch.object(speech.threading, 'Thread') as thread, \
                patch.object(speech, 'speech_model_path', return_value=None):
            speech.prewarm()
        thread.assert_not_called()


if __name__ == '__main__':
    unittest.main()
