"""Model folders, GPU detection and the cuBLAS runtime. No network, no nvidia-smi."""
import contextlib
import errno
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import zipfile
from unittest.mock import Mock, patch
from arabic_relay import locate, models, paths


def make_model(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json'):
        (folder / name).write_bytes(b'')
    return folder


def make_runtime(folder, names=('cublas64_12.dll', 'cublasLt64_12.dll')):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b'')
    return folder


class Models(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = temp.name
        self.data = Path(temp.name) / 'data'
        self.data.mkdir()
        for patcher in (patch.object(paths, 'DATA', self.data),
                        patch.object(models.site, 'getsitepackages', return_value=[]),
                        patch.dict(os.environ, {'HF_HUB_CACHE': str(Path(temp.name) / 'hub')})):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop('CUDA_PATH', None)
        models.gpu_info.cache_clear()
        self.addCleanup(models.gpu_info.cache_clear)

    def test_gpu_info_parses_nvidia_smi(self):
        done = Mock(returncode=0, stdout='NVIDIA GeForce RTX 4080 Laptop GPU, 12282, 616.92\n')
        with patch.object(models.subprocess, 'run', return_value=done) as run:
            self.assertEqual(models.gpu_info(), {'name': 'NVIDIA GeForce RTX 4080 Laptop GPU', 'vramGb': 12.0, 'driver': '616.92'})
        self.assertEqual(run.call_args.args[0][:2], ['nvidia-smi', '--query-gpu=name,memory.total,driver_version'])

    def test_gpu_info_is_none_without_driver(self):
        for outcome in (FileNotFoundError(), Mock(returncode=9, stdout=''), Mock(returncode=0, stdout='garbage\n')):
            models.gpu_info.cache_clear()
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with patch.object(models.subprocess, 'run', **kwargs):
                self.assertIsNone(models.gpu_info(), outcome)

    def test_gpu_runtime_prefers_app_runtime(self):
        runtime = make_runtime(self.data / 'runtime')
        os.environ['CUDA_PATH'] = str(Path(self.temp) / 'cuda')
        make_runtime(Path(self.temp) / 'cuda' / 'bin')
        self.addCleanup(os.environ.pop, 'CUDA_PATH', None)
        self.assertEqual(models.gpu_runtime_dir(), runtime)

    def test_gpu_runtime_needs_both_dlls(self):
        make_runtime(self.data / 'runtime', ('cublas64_12.dll',))
        self.assertIsNone(models.gpu_runtime_dir())

    def test_gpu_runtime_uses_cuda_path(self):
        cuda = make_runtime(Path(self.temp) / 'cuda' / 'bin')
        os.environ['CUDA_PATH'] = str(cuda.parent)
        self.addCleanup(os.environ.pop, 'CUDA_PATH', None)
        self.assertEqual(models.gpu_runtime_dir(), cuda)

    def test_frozen_app_skips_site_packages(self):
        # A PyInstaller app has no site-packages, and its site module may lack getsitepackages.
        runtime = make_runtime(self.data / 'runtime')
        with patch.object(models, 'site', object()), patch.object(models.sys, 'frozen', True, create=True):
            self.assertEqual(models.gpu_runtime_dir(), runtime)

    def test_model_label(self):
        for folder, label in (('x/models/large-v3-turbo', 'Whisper large-v3-turbo'),
                              ('x/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a3', 'Whisper large-v3-turbo'),
                              ('E/models/large-v3-edaa852', 'Whisper large-v3'), ('D:/نماذج/my model', 'Whisper (your model)')):
            self.assertEqual(models.model_label(Path(folder)), label)

    def test_downloaded_model_is_found(self):
        turbo = make_model(self.data / 'models' / 'large-v3-turbo')
        self.assertEqual(locate.whisper_model(None), turbo)

    def test_turbo_in_hf_cache_is_found(self):
        snapshot = make_model(Path(self.temp) / 'hub' / 'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo' / 'snapshots' / 'a')
        self.assertEqual(locate.whisper_model(None), snapshot)

    def test_delete_refuses_folder_outside_app_models(self):
        outside = make_model(Path(self.temp) / 'my models' / 'large-v3')
        with self.assertRaises(ValueError):
            models.delete_model(outside)
        self.assertTrue((outside / 'model.bin').exists())
        inside = make_model(self.data / 'models' / 'large-v3')
        with self.assertRaises(ValueError):
            models.delete_model(self.data / 'models')  # the models folder itself is not one model
        self.assertTrue((inside / 'model.bin').exists())

    def test_delete_removes_app_model(self):
        inside = make_model(self.data / 'models' / 'large-v3')
        self.assertTrue(models.app_owned(inside))
        models.delete_model(inside)
        self.assertFalse(inside.exists())

    def test_bytes_needed(self):
        self.assertEqual(models.bytes_needed('large-v3-turbo', False), 1_621_665_983 + 200_000_000)
        self.assertEqual(models.bytes_needed('large-v3', True), 3_090_835_702 + 553_162_896 + 771_191_808 + 200_000_000)


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves one byte string and honors `Range: bytes=N-`, like PyPI's file host."""
    def log_message(self, *_):
        pass

    def do_GET(self):
        data, start = self.server.data, 0
        self.server.ranges.append(self.headers.get('Range'))
        if self.headers.get('Range'):
            start = int(self.headers['Range'].removeprefix('bytes=').removesuffix('-'))
            self.send_response(206)
        else:
            self.send_response(200)
        self.send_header('Content-Length', str(len(data) - start))
        self.end_headers()
        self.wfile.write(data[start:])


class Downloads(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        local = patch.object(paths, 'DATA', self.root / 'data')
        local.start()
        self.addCleanup(local.stop)
        (self.root / 'data').mkdir()
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), RangeHandler)
        self.httpd.data, self.httpd.ranges = os.urandom(300_000), []
        self.ranges = self.httpd.ranges
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/w.whl'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def test_fetch_resumes_partial_file(self):
        data = self.httpd.data
        dest = self.root / 'w.whl'
        dest.with_suffix('.part').write_bytes(data[:100_000])
        models.fetch(self.url, dest, hashlib.sha256(data).hexdigest(), len(data), lambda *a: None)
        self.assertEqual(self.ranges, ['bytes=100000-'])
        self.assertEqual(dest.read_bytes(), data)

    def test_fetch_stops_at_the_expected_size(self):
        # A server sending more than the release said must not fill the disk.
        dest = self.root / 'w.whl'
        with self.assertRaises(ValueError):
            models.fetch(self.url, dest, hashlib.sha256(self.httpd.data).hexdigest(), 1000, lambda *a: None)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_suffix('.part').exists())

    def test_fetch_rejects_wrong_hash(self):
        dest = self.root / 'w.whl'
        with self.assertRaises(ValueError):
            models.fetch(self.url, dest, '0' * 64, len(self.httpd.data), lambda *a: None)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_suffix('.part').exists())

    def test_install_gpu_runtime_keeps_only_cublas(self):
        wheel = self.root / 'cublas.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            for name in ('cublas64_12.dll', 'cublasLt64_12.dll', 'nvblas64_12.dll'):
                archive.writestr('nvidia/cublas/bin/' + name, name)
            archive.writestr('nvidia/cublas/__init__.py', '')
        self.assertEqual(models.install_gpu_runtime(wheel), models.runtime_dir())
        self.assertEqual(sorted(p.name for p in models.runtime_dir().iterdir()), ['cublas64_12.dll', 'cublasLt64_12.dll'])
        self.assertEqual((models.runtime_dir() / 'cublasLt64_12.dll').read_text(), 'cublasLt64_12.dll')
        self.assertFalse(wheel.exists())

    def test_download_model_pins_revision_and_files(self):
        with patch('huggingface_hub.snapshot_download', side_effect=lambda **kw: str(make_model(Path(kw['local_dir'])))) as download:
            folder = models.download_model('large-v3', lambda *a: None)
        kwargs = download.call_args.kwargs
        self.assertEqual(folder, paths.DATA / 'models' / 'large-v3')
        self.assertEqual((kwargs['repo_id'], kwargs['revision'], Path(kwargs['local_dir']), kwargs['token']),
                         ('Systran/faster-whisper-large-v3', 'edaa852ec7e145841d8ffdb056a99866b5f0a478', paths.DATA / 'models' / 'large-v3', False))
        self.assertEqual(kwargs['allow_patterns'], ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*', 'preprocessor_config.json'])

    def test_download_progress_stops_before_return(self):
        reports = []
        def slow(**kw):
            time.sleep(0.8)  # long enough for the 0.5 s progress watcher to report
            return str(make_model(Path(kw['local_dir'])))
        def report(*progress):  # a report still in flight (0.5 s → 1.0 s) when the download returns at 0.8 s
            time.sleep(0.5)
            reports.append(progress)
        with patch('huggingface_hub.snapshot_download', side_effect=slow):
            models.download_model('large-v3', report)
        count = len(reports)
        self.assertGreater(count, 0)  # the in-flight report finished before download_model returned
        time.sleep(0.7)
        self.assertEqual(len(reports), count)  # no watcher line can land after (or inside) the modelPath line

    def test_folder_bytes_ignores_files_that_vanish(self):
        folder = self.root / 'f'
        folder.mkdir()
        (folder / 'a.incomplete').write_bytes(b'12345')
        (folder / 'b').write_bytes(b'123')
        real_stat = Path.stat
        def flaky(path, *args, **kwargs):
            if path.name == 'a.incomplete' and not kwargs and not args:
                raise FileNotFoundError(path)  # renamed by the library between listing and stat
            return real_stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', flaky):
            self.assertEqual(models.folder_bytes(folder), 3)

    def setup_lines(self, args, model=None, wheel=None, runtime=None):
        out = io.StringIO()
        model = model or (lambda name, report: (report('model', 5, 10), make_model(paths.DATA / 'models' / name))[1])
        with patch.object(models, 'download_model', side_effect=model), \
                patch.object(models, 'fetch', side_effect=wheel or (lambda *a: self.root / 'cublas.whl')) as fetch, \
                patch.object(models, 'install_gpu_runtime') as install, patch.object(models, 'gpu_runtime_dir', return_value=runtime), \
                patch('arabic_relay.speech_worker.watch_parent') as watch, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = models.setup_main(args)
        self.watch, self.fetch, self.install = watch, fetch, install
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_setup_main_prints_progress_then_path(self):
        code, lines = self.setup_lines(['4242', 'large-v3-turbo', '0'])
        self.assertEqual(code, 0)
        self.assertEqual(lines, [{'stage': 'model', 'done': 5, 'total': 10},
                                 {'modelPath': str(paths.DATA / 'models' / 'large-v3-turbo')}])
        self.fetch.assert_not_called()
        code, lines = self.setup_lines(['4242', 'large-v3-turbo', '1'])
        self.fetch.assert_called_once()
        self.install.assert_called_once()
        code, lines = self.setup_lines(['4242', 'large-v3-turbo', '1'], runtime=Path('installed'))
        self.fetch.assert_not_called()  # CUDA or an earlier download already provides cuBLAS

    def test_setup_main_maps_network_error(self):
        def offline(name, report):
            raise urllib.error.URLError('getaddrinfo failed')
        code, lines = self.setup_lines(['4242', 'large-v3', '0'], model=offline)
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1], {'error': 'The download stopped. Check your internet connection and try again; it resumes where it left off.'})

    def test_setup_main_reports_disk_full(self):
        def full(name, report):
            raise OSError(errno.ENOSPC, 'No space left on device')
        code, lines = self.setup_lines(['4242', 'large-v3', '0'], model=full)
        self.assertEqual(lines[-1], {'error': 'The disk is full. Free up space and try again; the download resumes where it left off.'})

    def test_setup_child_watches_parent(self):
        self.setup_lines(['4242', 'large-v3', '0'])
        self.watch.assert_called_once_with(4242)


if __name__ == '__main__':
    unittest.main()
