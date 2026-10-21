"""The public docs say what the spec promises users."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


def read(name):
    return (ROOT / name).read_text(encoding='utf-8')


class Docs(unittest.TestCase):
    def test_privacy_lists_every_connection(self):
        text = read('PRIVACY.md')
        for name in ('Codex', 'Claude Code', 'Cohere', 'Hugging Face', 'PyPI', 'GitHub Releases', 'No telemetry'):
            self.assertIn(name, text)

    def test_changelog_has_this_release(self):
        spec = importlib.util.spec_from_file_location('release_notes', ROOT / 'scripts' / 'release_notes.py')
        notes = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notes)
        self.assertTrue(notes.section(read('CHANGELOG.md'), '0.1.0'))

    def test_bug_template_asks_for_diagnostics(self):
        text = read('.github/ISSUE_TEMPLATE/bug_report.yml')
        self.assertIn('id: diagnostics', text)
        self.assertIn('required: true', text.split('id: diagnostics', 1)[1])

    def test_readme_links(self):
        text = read('README.md')
        for link in ('PRIVACY.md', 'LICENSE', 'https://github.com/Mahmoud6901/arabic-relay/releases'):
            self.assertIn(link, text)


if __name__ == '__main__':
    unittest.main()
