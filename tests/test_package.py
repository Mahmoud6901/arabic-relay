"""The package installs, versions and starts the same way from pip, from source and from the frozen exe."""
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest
from unittest.mock import patch
import arabic_relay

ROOT = Path(__file__).resolve().parent.parent
PROJECT = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))


class Package(unittest.TestCase):
    def test_one_version_source(self):
        self.assertEqual(arabic_relay.__version__, '0.1.0')
        self.assertEqual(PROJECT['project']['dynamic'], ['version'])
        self.assertEqual(PROJECT['tool']['setuptools']['dynamic']['version']['attr'], 'arabic_relay.__version__')

    def test_pip_install_gets_a_window_command(self):
        project = PROJECT['project']
        self.assertEqual(project['gui-scripts'], {'arabic-relay': 'arabic_relay.__main__:main'})
        self.assertIn('cryptography>=43', project['dependencies'])
        self.assertIn('setuptools>=77', PROJECT['build-system']['requires'])
        self.assertEqual(project['license-files'], ['LICENSE'])
        self.assertEqual(project['optional-dependencies']['build'], ['pyinstaller==6.22.3'])

    def test_worker_role_never_loads_the_window(self):
        from arabic_relay import __main__, speech_worker
        with patch.object(sys, 'argv', ['x', '--speech-worker', '--probe']), patch.object(speech_worker, 'cli') as cli:
            __main__.main()
        cli.assert_called_once_with(['--probe'])
        loaded = subprocess.run([sys.executable, '-I', '-c', "import sys, arabic_relay.__main__; print('webview' in sys.modules)"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(loaded.stdout.strip(), 'False', loaded.stderr)

    def test_missing_console_streams_are_replaced(self):
        # A windowed PyInstaller exe starts with no console streams.
        from arabic_relay import __main__, speech_worker
        saved = sys.stdout, sys.stderr
        try:
            sys.stdout = sys.stderr = None
            with patch.object(sys, 'argv', ['x', '--speech-worker']), patch.object(speech_worker, 'cli'):
                __main__.main()
            print('still works')
            print('still works', file=sys.stderr)
        finally:
            for stream in {sys.stdout, sys.stderr} - set(saved):
                stream.close()
            sys.stdout, sys.stderr = saved

    def test_license_is_mit(self):
        text = (ROOT / 'LICENSE').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('MIT License'))
        self.assertIn('Copyright (c) 2026 Mahmoud Adel Hassan', text)


if __name__ == '__main__':
    unittest.main()
