import os
import sys


def main():
    # A windowed (frozen) exe starts without console streams; print() and logging must still work.
    sys.stdout = sys.stdout or open(os.devnull, 'w')
    sys.stderr = sys.stderr or open(os.devnull, 'w')
    # Background roles first: they must never import the window toolkit.
    if '--speech-worker' in sys.argv:
        from .speech_worker import cli
        cli(sys.argv[sys.argv.index('--speech-worker') + 1:])
    elif '--setup' in sys.argv:
        from .models import setup_main
        sys.exit(setup_main(sys.argv[sys.argv.index('--setup') + 1:]))
    elif '--self-test' in sys.argv:
        from .diagnostics import self_test
        sys.exit(self_test())
    elif '--browser' in sys.argv:
        from .server import main as serve
        serve(open_browser=True)
    else:
        from .desktop import main as window
        window()


if __name__ == '__main__':
    main()
