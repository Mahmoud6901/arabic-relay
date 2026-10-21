"""First-run setup: disk check, the --setup child, cancel/resume, and the GPU self-check. No real downloads."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from arabic_relay import firstrun, models, paths, speech, store

GPU = {'name': 'NVIDIA GeForce RTX 4080 Laptop GPU', 'vramGb': 12.0, 'driver': '616.92'}
GPU_FAILED = "The GPU couldn't run the speech model, so speech will use the CPU. You can turn this off in Settings."


def make_model(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json'):
        (folder / name).write_bytes(b'')
    return folder


def child(lines, code=0, hold=None):
    """A fake --setup child: prints `lines`, optionally waits until killed, then exits with `code`."""
    def output():
        for line in lines:
            yield (json.dumps(line) + '\n').encode()
        if hold is not None:
            hold.wait(5)
    process = Mock()
    process.stdout = output()
    process.wait.return_value = code
    process.kill.side_effect = lambda: hold and hold.set()
    return process


class FirstRun(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        for patcher in (patch.object(paths, 'DATA', self.temp / 'data'), patch.object(firstrun, 'JOB', firstrun.SetupJob()),
                        patch.object(models, 'gpu_info', return_value=None),
                        patch.object(models, 'free_bytes', return_value=100_000_000_000),
                        # A test that regresses must never start a real download; fake children patch over this.
                        patch.object(firstrun.subprocess, 'Popen', side_effect=AssertionError('real --setup child started')),
                        patch.dict(os.environ, {'HF_HUB_CACHE': str(self.temp / 'nocache')})):
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.temp / 'data').mkdir()

    def run_job(self, process, check=None, name='large-v3-turbo', **start):
        with patch.object(firstrun.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(speech, 'check', side_effect=check or (lambda: {'device': 'CPU', 'fallback': False})):
            firstrun.JOB.start(name, **start)
            firstrun.JOB.thread.join(5)
        self.launch = launch
        return firstrun.JOB.progress()

    def model_line(self, name='large-v3-turbo'):
        return {'modelPath': str(make_model(paths.DATA / 'models' / name))}

    def test_start_refuses_when_disk_is_short(self):
        with patch.object(models, 'free_bytes', return_value=1_000_000_000):
            with self.assertRaisesRegex(ValueError, r'^Free up space first: this download needs 1\.8 GB and 1\.0 GB is free\.$'):
                firstrun.JOB.start('large-v3-turbo')

    def test_resume_counts_bytes_already_downloaded(self):
        partial = paths.DATA / 'models' / 'large-v3-turbo'
        partial.mkdir(parents=True)
        (partial / 'model.bin.incomplete').write_bytes(b'x' * 1000)
        need = models.bytes_needed('large-v3-turbo', False)
        with patch.object(models, 'free_bytes', return_value=need - 500):
            self.run_job(child([self.model_line()]))  # 500 short, but 1000 bytes are already here
        self.assertEqual(firstrun.JOB.progress()['state'], 'done')

    def test_installed_runtime_is_not_counted_as_free(self):
        runtime = models.runtime_dir()
        runtime.mkdir(parents=True)
        (runtime / 'cublas64_12.dll').write_bytes(b'x' * 1000)  # installed, so not part of this download
        need = models.bytes_needed('large-v3-turbo', False)
        with patch.object(models, 'free_bytes', return_value=need - 500), \
                patch.object(firstrun.subprocess, 'Popen', side_effect=AssertionError('must not start a real download')):
            with self.assertRaisesRegex(ValueError, '^Free up space first'):
                firstrun.JOB.start('large-v3-turbo')

    def test_job_runs_child_then_check(self):
        progress = self.run_job(child([{'stage': 'model', 'done': 5, 'total': 10}, self.model_line()]))
        self.assertEqual((progress['state'], progress['device'], progress['notice'], progress['error']), ('done', 'CPU', None, None))
        command = self.launch.call_args.args[0]
        self.assertEqual(command[-4:], ['--setup', str(os.getpid()), 'large-v3-turbo', '0'])
        self.assertNotIn('OPENAI_API_KEY', self.launch.call_args.kwargs['env'])
        self.assertEqual(store.get_setting('whisper_model_dir'), str(paths.DATA / 'models' / 'large-v3-turbo'))
        self.assertTrue(store.get_setting('setup_done'))
        self.assertFalse(firstrun.setup_needed())

    def test_gpu_present_asks_child_for_cublas(self):
        with patch.object(models, 'gpu_info', return_value=GPU), patch.object(models, 'gpu_runtime_dir', return_value=None):
            self.run_job(child([self.model_line('large-v3')]), check=lambda: {'device': 'GPU', 'fallback': False}, name='large-v3')
        self.assertEqual(self.launch.call_args.args[0][-1], '1')

    def test_gpu_check_failure_switches_to_cpu(self):
        with patch.object(models, 'gpu_info', return_value=GPU), patch.object(models, 'gpu_runtime_dir', return_value=Path('rt')):
            progress = self.run_job(child([self.model_line()]), check=lambda: {'device': 'CPU', 'fallback': True})
        self.assertEqual((progress['state'], progress['device'], progress['notice']), ('done', 'CPU', GPU_FAILED))
        self.assertEqual(store.get_setting('speech_device'), 'cpu')

    def test_child_error_is_reported(self):
        progress = self.run_job(child([{'stage': 'model', 'done': 5, 'total': 10}, {'error': 'x'}], code=1))
        self.assertEqual((progress['state'], progress['error']), ('error', 'x'))
        self.assertIsNone(store.get_setting('whisper_model_dir'))
        self.assertTrue(firstrun.setup_needed())

    def test_check_error_is_reported(self):
        def broken():
            raise ValueError('The speech engine stopped. Record again to restart it.')
        progress = self.run_job(child([self.model_line()]), check=broken)
        self.assertEqual((progress['state'], progress['error']), ('error', 'The speech engine stopped. Record again to restart it.'))

    def test_cancel_keeps_partial_files(self):
        partial = paths.DATA / 'models' / 'large-v3'
        partial.mkdir(parents=True)
        (partial / 'model.bin.incomplete').write_bytes(b'half')
        hold = threading.Event()
        process = child([{'stage': 'model', 'done': 4, 'total': 10}], code=1, hold=hold)
        with patch.object(firstrun.subprocess, 'Popen', return_value=process):
            firstrun.JOB.start('large-v3')
            self.assertEqual(firstrun.JOB.progress()['state'], 'downloading')
            self.assertEqual(firstrun.JOB.cancel()['state'], 'cancelled')
            firstrun.JOB.thread.join(5)
        process.kill.assert_called_once()
        self.assertEqual(firstrun.JOB.progress()['state'], 'cancelled')
        self.assertEqual((partial / 'model.bin.incomplete').read_bytes(), b'half')

    def test_fresh_start_removes_app_model_first(self):
        old = make_model(paths.DATA / 'models' / 'large-v3')
        (old / 'model.bin').write_bytes(b'damaged')
        seen = []
        def launch(*args, **kwargs):
            seen.append((old / 'model.bin').exists())
            return child([{'error': 'stop here'}], code=1)
        with patch.object(firstrun.subprocess, 'Popen', side_effect=launch):
            firstrun.JOB.start('large-v3', fresh=True)
            firstrun.JOB.thread.join(5)
        self.assertEqual(seen, [False])

    def test_second_start_is_refused(self):
        hold = threading.Event()
        with patch.object(firstrun.subprocess, 'Popen', return_value=child([], hold=hold)):
            firstrun.JOB.start('large-v3')
            with self.assertRaisesRegex(ValueError, r'^A download is already running\.$'):
                firstrun.JOB.start('large-v3')
            firstrun.JOB.cancel()
            firstrun.JOB.thread.join(5)

    def test_unknown_model_is_refused(self):
        with self.assertRaises(ValueError):
            firstrun.JOB.start('tiny')
        self.assertEqual(firstrun.JOB.progress()['state'], 'idle')

    def test_setup_needed(self):
        self.assertTrue(firstrun.setup_needed())
        self.assertEqual(firstrun.skip(), {'setupNeeded': False})
        self.assertFalse(firstrun.setup_needed())
        store.set_setting('setup_done', '')
        make_model(paths.DATA / 'models' / 'large-v3')
        self.assertFalse(firstrun.setup_needed())

    def test_checklist(self):
        with patch.object(models, 'gpu_runtime_dir', return_value=None), patch.object(firstrun.locate, 'translator_path', return_value='codex.exe'), \
                patch.object(firstrun.locate, 'claude_path', return_value=None), patch.object(firstrun.locate, 'wispr_flow_path', return_value=None):
            no_gpu = firstrun.checklist()
            with patch.object(models, 'gpu_info', return_value=GPU):
                gpu = firstrun.checklist()
        self.assertEqual(no_gpu, {'gpu': None, 'gpuRuntimeReady': False, 'codex': True, 'claude': False, 'wisprFlow': False,
                                  'freeGb': 100.0, 'recommended': 'large-v3-turbo',
                                  'models': {'large-v3': {'gb': 3.1}, 'large-v3-turbo': {'gb': 1.6}}, 'gpuDownloadGb': 0})
        self.assertEqual((gpu['gpu'], gpu['recommended'], gpu['gpuDownloadGb']), (GPU, 'large-v3', 0.55))

    def test_delete_current_model_only_when_app_owned(self):
        outside = make_model(self.temp / 'my models' / 'large-v3')
        speech.set_whisper_model(str(outside))
        with self.assertRaises(ValueError):
            speech.delete_current_model()
        self.assertEqual(store.get_setting('whisper_model_dir'), str(outside))
        owned = make_model(paths.DATA / 'models' / 'large-v3')
        speech.set_whisper_model(str(owned))
        self.assertEqual(speech.delete_current_model(), {'speechConfigured': False})
        self.assertFalse(owned.exists())
        self.assertTrue(outside.exists())

    def test_status_reports_missing_saved_folder(self):
        gone = make_model(self.temp / 'moved' / 'large-v3')
        speech.set_whisper_model(str(gone))
        for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json'):
            (gone / name).unlink()
        status = speech.status()
        self.assertEqual((status['missingModel'], status['speechConfigured']), (str(gone), False))
        speech.set_whisper_model(str(make_model(gone)))
        self.assertIsNone(speech.status()['missingModel'])


if __name__ == '__main__':
    unittest.main()
