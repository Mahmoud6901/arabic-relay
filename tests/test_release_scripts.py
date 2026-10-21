"""scripts/sign_release.py and scripts/release_notes.py. Never calls the real gh; never touches the real key."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from arabic_relay import updates

SCRIPTS = Path(__file__).resolve().parent.parent / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sign_release = load('sign_release')
release_notes = load('release_notes')


class Signing(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.key = self.root / 'keys' / 'release.key'
        self.public = sign_release.new_key(self.key)

    def test_new_key_never_overwrites(self):
        before = self.key.read_bytes()
        with self.assertRaises(FileExistsError):
            sign_release.new_key(self.key)
        self.assertEqual(self.key.read_bytes(), before)
        self.assertRegex(self.public, '^[0-9a-f]{64}$')

    def test_signature_verifies_with_updates(self):
        setup = self.root / 'ArabicRelay-Setup-0.2.0.exe'
        setup.write_bytes(b'MZ installer')
        signature = sign_release.sign(setup, self.key)
        self.assertEqual(len(signature), 64)
        self.assertTrue(updates.verify(setup, signature, self.public))

    def fake_gh(self, calls):
        def run(command, **kwargs):
            calls.append(command)
            if command[:3] == ['gh', 'release', 'download']:
                folder = Path(command[command.index('-D') + 1])
                (folder / 'ArabicRelay-Setup-0.2.0.exe').write_bytes(b'MZ installer')
            return subprocess.CompletedProcess(command, 0, '', '')
        return run

    def test_publish_refuses_a_key_that_doesnt_match(self):
        calls = []
        with patch.object(sign_release.subprocess, 'run', side_effect=self.fake_gh(calls)), \
                patch.object(updates, 'PUBLIC_KEY', '11' * 32):
            self.assertEqual(sign_release.main(['v0.2.0', '--key', str(self.key)]), 1)
        self.assertEqual([c[:3] for c in calls], [['gh', 'release', 'download']])

    def test_publish_runs_gh_in_order(self):
        calls = []
        with patch.object(sign_release.subprocess, 'run', side_effect=self.fake_gh(calls)), \
                patch.object(updates, 'PUBLIC_KEY', self.public):
            self.assertEqual(sign_release.main(['v0.2.0', '--key', str(self.key)]), 0)
        self.assertEqual([c[:3] for c in calls], [['gh', 'release', 'download'], ['gh', 'release', 'upload'], ['gh', 'release', 'edit']])
        self.assertEqual(calls[0][3:], ['v0.2.0', '-p', 'ArabicRelay-Setup-*.exe', '-D', calls[0][-1]])
        self.assertTrue(calls[1][4].endswith('ArabicRelay-Setup-0.2.0.exe.sig'))
        self.assertIn('--clobber', calls[1])
        self.assertEqual(calls[2][3:], ['v0.2.0', '--draft=false'])


class Notes(unittest.TestCase):
    CHANGELOG = '# Changelog\n\n## [0.2.0] - 2026-11-01\n\n- Faster.\n\n## [0.1.0] - 2026-10-21\n\n- First release.\n'

    def test_release_notes_section(self):
        self.assertEqual(release_notes.section(self.CHANGELOG, '0.2.0'), '- Faster.')
        self.assertEqual(release_notes.section(self.CHANGELOG, '0.1.0'), '- First release.')
        with self.assertRaises(ValueError):
            release_notes.section(self.CHANGELOG, '0.3.0')


if __name__ == '__main__':
    unittest.main()
