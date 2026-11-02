"""Foreground delivery tests. Fake Windows calls only; never types into a real app."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from arabic_relay import desktop, inline, paste, paths


class FakeWindows:
    def __init__(self):
        self.user = SimpleNamespace(CloseClipboard=Mock())
        self.seq = 10
        self.rows = [(13, b'original')]
        self.injected = []
        self.wait_modifiers = Mock()
        self.restore_target = Mock()
        self.open_clipboard = Mock()

    def snapshot(self):
        return list(self.rows)

    def sequence(self):
        return self.seq

    def write(self, rows):
        self.rows = list(rows)
        self.seq += 1

    def put_text(self, text):
        self.write([(13, text.encode('utf-16-le') + b'\0\0')])

    def gesture(self, target, attempted):
        attempted()
        self.injected.append(target)


class ClipboardDelivery(unittest.TestCase):
    def setUp(self):
        self.win = FakeWindows()
        self.paster = paste.Paster(self.win)
        self.target = paste.Target(123, 456, 789, 'Notepad')
        self.timer = patch.object(paste.threading, 'Timer').start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.release)

    def release(self):
        if self.paster.pending:
            self.paster.restore_and_release([(13, b'original')], self.win.seq)

    def test_pastes_exact_unicode_and_restores_original_clipboard(self):
        attempted = Mock()
        result = self.paster.paste(self.target, '  راجع MapView.\n', attempted)
        self.assertEqual(result['status'], 'paste_requested')
        self.assertEqual(self.win.rows[0][1], '  راجع MapView.\n'.encode('utf-16-le') + b'\0\0')
        attempted.assert_called_once()
        args = self.timer.call_args.kwargs['args']
        self.paster.restore_and_release(*args)
        self.assertEqual(self.win.rows, [(13, b'original')])

    def test_new_clipboard_content_is_never_overwritten_by_restore(self):
        self.paster.paste(self.target, 'Example', Mock())
        args = self.timer.call_args.kwargs['args']
        self.win.write([(13, b'user copied something else')])
        self.paster.restore_and_release(*args)
        self.assertEqual(self.win.rows, [(13, b'user copied something else')])

    def test_sequence_is_sampled_after_windows_commits_on_close(self):
        def close():
            self.win.seq += 1
        self.win.user.CloseClipboard.side_effect = close
        result = self.paster.paste(self.target, 'Exact prompt', Mock())
        self.assertEqual(result['status'], 'paste_requested')
        self.assertEqual(self.timer.call_args.kwargs['args'][1], self.win.seq)

    def test_missing_target_focus_failure_and_unsupported_clipboard_never_type(self):
        attempted = Mock()
        with self.assertRaises(ValueError):
            self.paster.paste(None, 'Example', attempted)
        self.win.restore_target.side_effect = ValueError('Focus changed')
        with self.assertRaises(ValueError):
            self.paster.paste(self.target, 'Example', attempted)
        self.win.restore_target.side_effect = None
        self.win.snapshot = Mock(side_effect=ValueError('Cannot preserve clipboard'))
        with self.assertRaises(ValueError):
            self.paster.paste(self.target, 'Example', attempted)
        attempted.assert_not_called()
        self.assertEqual(self.win.injected, [])
        self.assertFalse(paste.LOCK.locked())

    def test_changed_clipboard_or_partial_input_cannot_silently_retry(self):
        attempted = Mock()
        self.win.wait_modifiers.side_effect = [None, ValueError('Held shortcut')]
        with self.assertRaises(ValueError):
            self.paster.paste(self.target, 'Example', attempted)
        attempted.assert_not_called()
        self.assertEqual(self.win.rows, [(13, b'original')])
        self.win.wait_modifiers.side_effect = None
        def partial(target, callback):
            callback()
            raise ValueError('Partial input')
        self.win.gesture = partial
        with self.assertRaises(ValueError):
            self.paster.paste(self.target, 'Example', attempted)
        attempted.assert_called_once()
        self.assertEqual(self.win.rows, [(13, b'original')])

    def test_keyboard_sequence_is_only_control_v_never_enter(self):
        win = paste.Windows.__new__(paste.Windows)
        win.check_target = Mock()
        observed = []
        def send(count, inputs, size):
            observed.extend((event.data.keyboard.key, event.data.keyboard.flags) for event in inputs)
            return count
        win.user = SimpleNamespace(SendInput=send)
        win.gesture(self.target, Mock())
        self.assertEqual(observed, [(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)])
        self.assertNotIn(0x0D, [key for key, _ in observed])

    def test_target_identity_and_input_focus_must_still_match(self):
        win = paste.Windows.__new__(paste.Windows)
        win.user = SimpleNamespace(GetForegroundWindow=lambda: 123)
        win.identity = lambda _: (456, 789)
        win.check_target(self.target)
        win.identity = lambda _: (456, 111)
        with self.assertRaises(ValueError):
            win.check_target(self.target)

    def test_activation_restores_the_original_child_before_paste(self):
        win = paste.Windows.__new__(paste.Windows)
        focused = [111]
        win.identity = lambda _: (456, focused[0])
        win.kernel = SimpleNamespace(GetCurrentThreadId=lambda: 20)
        win.user = SimpleNamespace(
            IsWindow=lambda _: True, IsIconic=lambda _: False,
            GetForegroundWindow=lambda: 123, SetForegroundWindow=Mock(),
            IsChild=lambda parent, child: (parent, child) == (123, 789),
            GetWindowThreadProcessId=lambda *args: 30,
            AttachThreadInput=Mock(return_value=True),
            SetFocus=lambda child: focused.__setitem__(0, child))
        win.restore_target(self.target)
        win.check_target(self.target)
        self.assertEqual(win.user.AttachThreadInput.call_args_list[0].args, (20, 30, True))
        self.assertEqual(win.user.AttachThreadInput.call_args_list[-1].args, (20, 30, False))
        win.identity = lambda _: (999, 789)
        with self.assertRaises(ValueError):
            win.check_target(self.target)


class NativeBridge(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = patch.object(paths, 'DATA', Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.capture = SimpleNamespace(lock=threading.Lock(), target=None,
                    next_target=paste.Target(123, 456, 789, 'Notepad'),
                    capture_id=None, paste_attempted=False, paster=Mock(), window=Mock())
        self.api = desktop.DesktopApi(self.capture, quick=True)
        self.token = desktop.server.CSRF
        self.armed = self.api.begin_capture(self.token)
        def deliver(target, text, attempted):
            attempted()
            return {'status': 'paste_requested', 'app': 'Notepad', 'message': 'Paste requested'}
        self.capture.paster.paste.side_effect = deliver

    def test_bridge_requires_native_capture_and_csrf_and_rejects_duplicates(self):
        body = {'english': 'Exact prompt', 'captureId': self.armed['captureId']}
        with self.assertRaises(ValueError):
            self.api.paste_to_target(body, 'bad token')
        with self.assertRaises(ValueError):
            desktop.DesktopApi(self.capture).paste_to_target(body, self.token)
        with self.assertRaises(ValueError):
            self.api.paste_to_target({**body, 'captureId': 'stale'}, self.token)
        self.assertEqual(self.api.paste_to_target(body, self.token)['status'], 'paste_requested')
        with self.assertRaises(ValueError):
            self.api.paste_to_target(body, self.token)
        self.capture.paster.paste.assert_called_once()
        self.assertEqual(desktop.history.entries()['entries'][0]['english'], 'Exact prompt')

    def test_inline_proof_checked_before_any_paste(self):
        req = {'text': 'Check MapView.', 'revision': 3, 'requestId': 'paste-inline-proof-123456'}
        proof = inline.process(req)['proof']
        body = {'mode': 'inline', 'english': req['text'], 'inlineRevision': 3,
                'inlineProof': proof, 'captureId': self.armed['captureId']}
        with self.assertRaises(ValueError):
            self.api.paste_to_target({**body, 'english': 'Changed text'}, self.token)
        self.capture.paster.paste.assert_not_called()
        self.api.paste_to_target(body, self.token)
        self.capture.paster.paste.assert_called_once()


if __name__ == '__main__':
    unittest.main()
