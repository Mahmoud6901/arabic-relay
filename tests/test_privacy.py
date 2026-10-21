"""Publishable files must never carry personal data: this repository is public."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_NAMES = re.compile(r'(\.sqlite3|\.exe|\.bin)$|(^|/)catalog\.json$')
FORBIDDEN_TEXT = [re.compile(p) for p in (
    r'(?<![A-Za-z0-9])[D-Zd-z]:[\\/]+[A-Za-z]',       # paths on personal data drives, also as "D:\\x"
    r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]+Users[\\/]+\w',  # someone's home folder
    r'Personal_Work',
    r'sk-[A-Za-z0-9_-]{20,}',                         # API keys
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
)]
BINARY = {'.ttf', '.ico', '.png', '.jpg', '.wav'}


def offenders(root):
    # quotepath=off keeps non-ASCII (Arabic) file names readable instead of "\331\206…".
    listed = subprocess.run(['git', '-c', 'core.quotepath=off', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=root, capture_output=True, text=True, encoding='utf-8', check=True).stdout.split('\0')
    found = []
    for name in filter(None, listed):
        if name == 'tests/test_privacy.py':
            continue
        if FORBIDDEN_NAMES.search(name):
            found.append(name)
            continue
        path = Path(root) / name
        if path.suffix.lower() in BINARY or not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        found += [f'{name}: {m.group(0)}' for pattern in FORBIDDEN_TEXT for m in pattern.finditer(text)]
    return found


class PrivacyGuard(unittest.TestCase):
    def test_no_personal_data_in_publishable_files(self):
        self.assertEqual(offenders(ROOT), [])

    def test_guard_sees_arabic_names_and_escaped_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(['git', 'init', '-q'], cwd=folder, check=True)
            root = Path(folder)
            (root / 'نم.sqlite3').write_bytes(b'')
            (root / 'مثال.txt').write_text('model in D:/models/large', encoding='utf-8')
            (root / 'escaped.py').write_text("path = 'D:\\\\models\\\\large'\n", encoding='utf-8')
            (root / 'lower.txt').write_text('saved under e:\\data', encoding='utf-8')
            (root / 'home.json').write_text('{"p": "C:\\\\Users\\\\someone"}', encoding='utf-8')
            found = offenders(root)
        self.assertIn('نم.sqlite3', found)
        for name in ('مثال.txt', 'escaped.py', 'lower.txt', 'home.json'):
            self.assertTrue(any(item.startswith(name + ':') for item in found), (name, found))


if __name__ == '__main__':
    unittest.main()
