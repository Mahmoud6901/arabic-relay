"""Where the app's files live. Read `paths.DATA` at call time so tests can patch it."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('ARABIC_RELAY_DATA_DIR') or Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Arabic Relay')
DATA.mkdir(parents=True, exist_ok=True)


def self_command(*args):
    """Relaunch this program in another role: the frozen exe directly, or the package in development."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    return [sys.executable, '-I', '-m', 'arabic_relay', *args]
