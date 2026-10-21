"""Local transcription never uploads audio or sends T3 prompts."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from arabic_relay import server, locate, models, paths, speech, speech_worker, store, t3, translate


def make_model(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json'):
        (folder / name).write_bytes(b'')
    return folder


class SpeechBoundary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        local = patch.object(paths, 'DATA', Path(temp.name))
        local.start()
        self.addCleanup(local.stop)
        restore = patch.object(translate, 'restore_english', side_effect=lambda text, vocabulary: {
            'text': text, 'rawText': text, 'termsRestored': False})
        restore.start()
        self.addCleanup(restore.stop)
        no_gpu = patch.object(models, 'gpu_info', return_value=None)  # never run nvidia-smi in tests
        no_gpu.start()
        self.addCleanup(no_gpu.stop)

    def process(self, data, code=0):
        result = Mock(returncode=code)
        result.communicate.return_value = (json.dumps(data).encode(), b'')
        return result

    def test_mixed_words_reach_only_local_worker(self):
        mixed = 'ضيف dark mode في react-dashboard'
        process = self.process({'text': mixed})
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(server.urllib.request, 'urlopen') as upload, patch.object(t3, 'tool') as t3_tool:
            response = speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm', 'dark mode, react-dashboard')
        self.assertEqual(response['text'], mixed)
        self.assertIn('--speech-worker', launch.call_args.args[0])
        self.assertEqual(launch.call_args.kwargs['env']['HF_HUB_OFFLINE'], '1')
        header, audio = process.communicate.call_args.args[0].split(b'\n', 1)
        self.assertEqual(json.loads(header)['keywords'], ['dark mode', 'react-dashboard'])
        self.assertEqual(audio, b'\x1aE\xdf\xa3fake')
        upload.assert_not_called()
        t3_tool.assert_not_called()

    def test_invalid_audio_or_missing_model_cannot_launch_worker(self):
        with patch.object(speech.subprocess, 'Popen') as launch, \
                patch.object(speech, 'speech_model_path', return_value=None):
            for audio, mime in [(b'fake', 'audio/webm'), (b'\x1aE\xdf\xa3', 'text/html'), (b'\x1aE\xdf\xa3', 'audio/webm')]:
                with self.assertRaises(ValueError):
                    speech.transcribe(audio, mime)
        launch.assert_not_called()

    def test_local_transcript_returns_without_waiting_for_codex(self):
        raw = 'الديدلاين بعد الميتنج وخلي React زي ما هو'
        process = self.process({'text': raw})
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', return_value=process), \
                patch.object(translate, 'restore_english', side_effect=AssertionError('Transcription must not wait for Codex')) as restore:
            result = speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm', 'React')
        self.assertEqual(result['text'], raw)
        self.assertEqual(result['rawText'], raw)
        restore.assert_not_called()

    def test_auto_spelling_preference_does_not_block_transcription(self):
        self.assertFalse(store.auto_spelling_enabled())
        store.set_auto_spelling(True)
        self.assertTrue(store.auto_spelling_enabled())
        raw = 'نص التفريغ الأصلي'
        process = self.process({'text': raw})
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', return_value=process), \
                patch.object(translate, 'restore_english', side_effect=ValueError('Plan limit reached')):
            result = speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
        self.assertEqual(result['text'], raw)
        self.assertEqual(result['rawText'], raw)
        self.assertNotIn('spellingError', result)
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())
        store.set_auto_spelling(False)
        self.assertFalse(store.auto_spelling_enabled())
        for value in [None, 1, 'false']:
            with self.assertRaises(ValueError):
                store.set_auto_spelling(value)

    def test_failed_worker_never_retries_or_falls_back_to_cloud(self):
        process = self.process({'error': 'LOCAL_SPEECH_FAILED', 'detail': 'private test detail'}, 1)
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(server.urllib.request, 'urlopen') as upload:
            with self.assertRaises(ValueError) as caught:
                speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
        self.assertNotIn('private test detail', str(caught.exception))
        launch.assert_called_once()
        upload.assert_not_called()
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_timeout_kills_worker_and_unlocks(self):
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired('worker', 300), (b'', b'')]
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', return_value=process):
            with self.assertRaises(ValueError):
                speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
        process.kill.assert_called_once()
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_worker_command_dev(self):
        self.assertEqual(paths.self_command('--speech-worker', '1', 'f', 'cpu'),
                         [sys.executable, '-I', '-m', 'arabic_relay', '--speech-worker', '1', 'f', 'cpu'])

    def test_worker_command_frozen(self):
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(paths.self_command('--speech-worker', '1', 'f', 'cpu'),
                             [sys.executable, '--speech-worker', '1', 'f', 'cpu'])

    def test_worker_command_keeps_arabic_path(self):
        folder = Path('D:/نماذج/large v3')
        with patch.object(speech, 'speech_model_path', return_value=folder), patch.object(speech, 'speech_device', return_value='cpu'), \
                patch.object(speech.subprocess, 'Popen', return_value=self.process({'text': 'نص', 'device': 'CPU', 'fallback': False})) as launch:
            speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
        self.assertEqual(launch.call_args.args[0][-3:], [str(os.getpid()), str(folder), 'cpu'])

    def test_device_is_cpu_without_gpu(self):
        with patch.object(models, 'gpu_runtime_dir', return_value=Path('runtime')):
            self.assertEqual(speech.speech_device(), 'cpu')

    def test_cpu_only_setting_forces_cpu(self):
        with patch.object(models, 'gpu_info', return_value={'name': 'GPU', 'vramGb': 12.0, 'driver': '1'}), \
                patch.object(models, 'gpu_runtime_dir', return_value=Path('runtime')):
            self.assertEqual(speech.speech_device(), 'cuda')
            self.assertEqual(speech.set_cpu_only(True), {'cpuOnly': True})
            self.assertEqual(speech.speech_device(), 'cpu')
            self.assertEqual(speech.set_cpu_only(False), {'cpuOnly': False})
            self.assertEqual(speech.speech_device(), 'cuda')
        with self.assertRaises(ValueError):
            speech.set_cpu_only('yes')

    def test_gpu_without_runtime_uses_cpu(self):
        with patch.object(models, 'gpu_info', return_value={'name': 'GPU', 'vramGb': 12.0, 'driver': '1'}), \
                patch.object(models, 'gpu_runtime_dir', return_value=None):
            self.assertEqual(speech.speech_device(), 'cpu')

    def crashed(self):
        process = Mock(returncode=3221225477)
        process.communicate.return_value = (b'', b'access violation')
        return process

    def test_crash_relaunches_once(self):
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', side_effect=[self.crashed(), self.process({'text': 'نص', 'device': 'CPU', 'fallback': False})]) as launch:
            self.assertEqual(speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')['text'], 'نص')
        self.assertEqual(launch.call_count, 2)

    def test_cuda_crash_relaunches_on_cpu(self):
        with patch.object(speech, 'speech_model_path', return_value=Path('x/models/large-v3')), \
                patch.object(speech, 'speech_device', return_value='cuda'), \
                patch.object(speech.subprocess, 'Popen', side_effect=[self.crashed(), self.process({'text': 'نص', 'device': 'CPU', 'fallback': False})]) as launch:
            result = speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
        self.assertEqual([call.args[0][-1] for call in launch.call_args_list], ['cuda', 'cpu'])
        self.assertEqual(result['notice'], 'The GPU was unavailable, so this recording ran on the CPU.')

    def test_second_crash_reports_engine_stopped(self):
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', side_effect=[self.crashed(), self.crashed()]) as launch:
            with self.assertRaisesRegex(ValueError, r'^The speech engine stopped\. Record again to restart it\.$'):
                speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
        self.assertEqual(launch.call_count, 2)
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_worker_error_code_is_not_retried(self):
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', return_value=self.process({'error': 'INVALID_AUDIO'}, 1)) as launch:
            with self.assertRaisesRegex(ValueError, 'could not be decoded'):
                speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
        launch.assert_called_once()

    def test_gpu_fallback_notice(self):
        with patch.object(speech, 'speech_model_path', return_value=Path('x/models/large-v3')), \
                patch.object(speech.subprocess, 'Popen', return_value=self.process({'text': 'x', 'device': 'CPU', 'fallback': True})):
            result = speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
        self.assertEqual(result['notice'], 'The GPU was unavailable, so this recording ran on the CPU.')
        self.assertEqual(result['device'], 'CPU')
        self.assertEqual(result['model'], 'Whisper large-v3')

    def test_check_sends_silent_wav_in_check_mode(self):
        process = self.process({'text': '', 'device': 'GPU', 'fallback': False})
        with patch.object(speech, 'speech_model_path', return_value=Path('local-model')), \
                patch.object(speech.subprocess, 'Popen', return_value=process):
            self.assertEqual(speech.check(), {'device': 'GPU', 'fallback': False})
        header, audio = process.communicate.call_args.args[0].split(b'\n', 1)
        self.assertTrue(json.loads(header)['check'])
        self.assertTrue(audio.startswith(b'RIFF') and audio[8:12] == b'WAVE')
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_cuda_failure_falls_back_to_cpu(self):
        calls = []
        def fake_model(folder, device, compute_type, local_files_only):
            calls.append((device, compute_type))
            if device == 'cuda':
                raise RuntimeError('CUDA failed with error out of memory')
            return Mock(transcribe=Mock(return_value=(iter([Mock(text=' مرحبا')]), None)))
        with patch('faster_whisper.WhisperModel', side_effect=fake_model), patch.object(speech_worker.sys, 'stderr', io.StringIO()):
            result = speech_worker.run_model([0.0] * 16000, 'folder', 'cuda', [])
        self.assertEqual(calls, [('cuda', 'float16'), ('cpu', 'int8')])
        self.assertEqual(result, {'text': 'مرحبا', 'device': 'CPU', 'fallback': True})

    def test_status_reports_model_device_and_ownership(self):
        owned = make_model(paths.DATA / 'models' / 'large-v3-turbo')
        with patch.dict(os.environ, {'HF_HUB_CACHE': str(paths.DATA / 'nocache')}), \
                patch.object(models, 'gpu_info', return_value={'name': 'NVIDIA RTX', 'vramGb': 12.0, 'driver': '1'}), \
                patch.object(models, 'gpu_runtime_dir', return_value=Path('runtime')):
            self.assertEqual(speech.status(), {'speechConfigured': True, 'speechModel': 'Whisper large-v3-turbo',
                                               'modelPath': str(owned), 'speechDevice': 'GPU', 'cpuOnly': False,
                                               'gpu': 'NVIDIA RTX', 'modelOwned': True, 'missingModel': None})
            speech.set_cpu_only(True)
            self.assertEqual(speech.status()['speechDevice'], 'CPU')
            self.assertTrue(speech.status()['cpuOnly'])

    def test_status_without_model(self):
        with patch.dict(os.environ, {'HF_HUB_CACHE': str(paths.DATA / 'nocache')}):
            status = speech.status()
        self.assertEqual((status['speechConfigured'], status['speechModel'], status['modelPath'], status['modelOwned'], status['gpu']),
                         (False, None, None, False, None))

    def test_saved_model_setting_round_trips(self):
        folder = make_model(paths.DATA / 'm')
        self.assertEqual(speech.set_whisper_model(str(folder))['modelPath'], str(folder))
        self.assertEqual(speech.speech_model_path(), folder)

    def test_invalid_model_folder_is_not_saved(self):
        with self.assertRaises(ValueError):
            speech.set_whisper_model(str(paths.DATA / 'missing'))
        with patch.dict(server.os.environ, {'HF_HUB_CACHE': str(paths.DATA / 'nocache')}):
            self.assertIsNone(speech.speech_model_path())

    def test_wispr_launch_uses_only_detected_executable_and_sends_no_audio(self):
        with patch.object(locate, 'wispr_flow_path', return_value=Path('C:/installed/Wispr Flow.exe')), \
                patch.object(speech.subprocess, 'Popen') as launch, patch.object(server.webbrowser, 'open') as browser, \
                patch.object(server.urllib.request, 'urlopen') as upload, patch.object(t3, 'tool') as t3_tool:
            self.assertEqual(speech.open_wispr_flow(), {'installed': True})
        self.assertEqual(launch.call_args.args[0], ['C:\\installed\\Wispr Flow.exe'])
        browser.assert_not_called()
        upload.assert_not_called()
        t3_tool.assert_not_called()

    def test_missing_wispr_opens_only_official_download_page(self):
        with patch.object(locate, 'wispr_flow_path', return_value=None), patch.object(speech.subprocess, 'Popen') as launch, \
                patch.object(server.webbrowser, 'open', return_value=True) as browser:
            self.assertEqual(speech.open_wispr_flow(), {'installed': False})
        browser.assert_called_once_with('https://wisprflow.ai/downloads')
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
