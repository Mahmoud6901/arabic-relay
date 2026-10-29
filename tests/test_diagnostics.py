"""Logs, crash capture and the Copy diagnostics report. Never reads transcripts; never starts real tools."""
import json
import logging
import logging.handlers
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from arabic_relay import __version__, diagnostics, locate, models, paths, store


class Diagnostics(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        patcher = patch.object(paths, 'DATA', self.data)
        patcher.start()
        self.addCleanup(patcher.stop)
        hooks = sys.excepthook, threading.excepthook
        handlers = list(logging.getLogger().handlers)

        def restore():
            sys.excepthook, threading.excepthook = hooks
            for handler in set(logging.getLogger().handlers) - set(handlers):
                logging.getLogger().removeHandler(handler)
                handler.close()
        self.addCleanup(restore)

    def test_log_rotates_at_one_megabyte(self):
        diagnostics.start_logging()
        rotating = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertEqual(len(rotating), 1)
        self.assertEqual(Path(rotating[0].baseFilename), self.data / 'logs' / 'relay.log')
        self.assertEqual((rotating[0].maxBytes, rotating[0].backupCount), (1_000_000, 2))

    def test_unhandled_exception_is_recorded(self):
        diagnostics.start_logging()
        try:
            raise ValueError('boom')
        except ValueError as error:
            sys.excepthook(ValueError, error, error.__traceback__)
        self.assertIn('ValueError: boom', (self.data / 'logs' / 'crash.txt').read_text(encoding='utf-8'))
        self.assertTrue(diagnostics.crashed())
        self.assertEqual(diagnostics.dismiss_crash(), {'crashed': False})
        self.assertFalse(diagnostics.crashed())
        thread = threading.Thread(target=lambda: 1 / 0)
        thread.start()
        thread.join()
        self.assertIn('ZeroDivisionError', (self.data / 'logs' / 'crash.txt').read_text(encoding='utf-8'))

    def test_scrub_hides_the_username(self):
        home = Path('C:/', 'Users', 'someone')
        with patch.object(Path, 'home', return_value=home):
            for path in (str(home), str(home).upper(), str(home).replace('\\', '/'), 'c:/users/SomeOne/AppData'):
                scrubbed = diagnostics.scrub(f'folder {path}\\x')
                self.assertNotIn('someone', scrubbed.lower(), scrubbed)
                self.assertIn('<user>', scrubbed)
            self.assertEqual(diagnostics.scrub('someone said hi'), 'someone said hi')
            self.assertEqual(diagnostics.scrub(str(Path('C:/', 'Users', 'someoneelse'))), str(Path('C:/', 'Users', 'someoneelse')))

    def test_report_lists_versions_and_never_transcripts(self):
        with store.database() as db:
            db.execute('INSERT INTO approvals VALUES (?, ?, ?)', ('a', '{"english": "SECRET-TRANSCRIPT"}', 0))
        gpu = {'name': 'RTX 5070', 'vramGb': 12.0, 'driver': '600.1'}
        with patch.object(models, 'gpu_info', return_value=gpu), patch.object(locate, 'tool_path', return_value=None):
            report = diagnostics.report()
        for expected in (f'Arabic Relay {__version__}', 'Windows', 'RTX 5070', '600.1', 'Codex: not found', 'Claude Code: not found',
                         'Wispr Flow: not found', 'Data folder: '):
            self.assertIn(expected, report)
        self.assertNotIn('SECRET-TRANSCRIPT', report)

    def test_report_includes_the_last_crash(self):
        diagnostics.record_crash('Traceback: KeyError: x')
        with patch.object(models, 'gpu_info', return_value=None), patch.object(locate, 'tool_path', return_value=None):
            report = diagnostics.report()
        self.assertIn('GPU: none found', report)
        self.assertIn('Last crash:\nTraceback: KeyError: x', report)


class SelfTest(unittest.TestCase):
    def test_self_test_passes_from_source(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'ARABIC_RELAY_DATA_DIR': folder}
            env.pop('ARABIC_RELAY_PORT', None)
            done = subprocess.run([sys.executable, '-I', '-m', 'arabic_relay', '--self-test'], capture_output=True, text=True,
                                  timeout=180, env=env)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]), {'status': True, 'worker': True, 'version': __version__})

    def test_self_test_fails_when_the_worker_fails(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, 'DATA', Path(folder)), \
                patch.object(models, 'gpu_info', return_value=None), \
                patch.object(diagnostics.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'boom')), \
                patch('builtins.print') as shown:
            self.assertEqual(diagnostics.self_test(), 1)
        self.assertEqual(json.loads(shown.call_args.args[0]), {'status': True, 'worker': False, 'version': __version__})


if __name__ == '__main__':
    unittest.main()
