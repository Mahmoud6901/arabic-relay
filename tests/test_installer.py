"""Installer and release-script rules that only show up on a user's machine."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent


class Installer(unittest.TestCase):
    def test_uninstall_deletes_only_what_it_installed(self):
        # A user can install into an existing folder; uninstalling must not empty it.
        script = (ROOT / 'installer' / 'ArabicRelay.iss').read_text(encoding='utf-8')
        self.assertNotRegex(script, re.compile(r'^Type:\s*filesandordirs;\s*Name:\s*"\{app\}"\s*$', re.IGNORECASE | re.MULTILINE))

    def test_smoke_never_runs_next_to_a_real_install(self):
        # The smoke install shares the AppId: its uninstall would remove the real shortcut and Apps entry.
        build = (ROOT / 'scripts' / 'build.ps1').read_text(encoding='utf-8')
        self.assertIn('{B981CB91-79B0-4049-ABF8-C2A11A94560B}_is1', build)
        releasing = (ROOT / 'docs' / 'RELEASING.md').read_text(encoding='utf-8').split('## Tag and sign')[0]
        self.assertNotIn('-Smoke', releasing)


if __name__ == '__main__':
    unittest.main()
