"""Print one version's section of CHANGELOG.md: `python scripts/release_notes.py 0.2.0`."""
from pathlib import Path
import re
import sys


def section(changelog, version):
    match = re.search(rf'^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)', changelog, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f'CHANGELOG.md has no section for {version}.')
    return match.group(1).strip()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')  # the changelog has non-ASCII punctuation
    print(section((Path(__file__).resolve().parent.parent / 'CHANGELOG.md').read_text(encoding='utf-8'), sys.argv[1]))
