"""Installer and release-script rules that only show up on a user's machine."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent


class Installer(unittest.TestCase):
    def test_frozen_build_only_includes_public_application_assets(self):
        # Ignored legacy data next to source must not become part of a distributable build.
        spec = (ROOT / 'installer' / 'ArabicRelay.spec').read_text(encoding='utf-8')
        self.assertIn("includes=['index.html', 'fonts/*', 'assets/*', 'relay-claude-hook.ps1']", spec)
        self.assertNotIn("collect_data_files('arabic_relay')", spec)

    def test_uninstall_deletes_only_what_it_installed(self):
        # A user can install into an existing folder; uninstalling must not empty it.
        script = (ROOT / 'installer' / 'ArabicRelay.iss').read_text(encoding='utf-8')
        self.assertNotRegex(script, re.compile(r'^Type:\s*filesandordirs;\s*Name:\s*"\{app\}"\s*$', re.IGNORECASE | re.MULTILINE))

    def test_smoke_never_runs_next_to_a_real_install(self):
        # The smoke install shares the AppId: its uninstall would remove the real shortcut and Apps entry.
        build = (ROOT / 'scripts' / 'build.ps1').read_text(encoding='utf-8')
        smoke = build.split('if ($Smoke) {', 1)[1]
        # Release documentation is local-only. Check the executable guard before the install instead.
        guard = re.search(
            r"if \(Test-Path 'HKCU:[^']*\{B981CB91-79B0-4049-ABF8-C2A11A94560B\}_is1'\) \{\s*"
            r"throw 'Arabic Relay is installed here;[^']*'\s*\}", smoke)
        self.assertIsNotNone(guard)
        self.assertLess(guard.end(), smoke.index('Start-Process $setup'))


if __name__ == '__main__':
    unittest.main()
