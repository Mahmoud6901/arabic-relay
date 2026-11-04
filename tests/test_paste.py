"""Foreground delivery tests. Fake Windows calls only; never types into a real app."""
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import threading
import time
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
        self.settle_clipboard = Mock()
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

    def test_paste_keys_wait_for_the_clipboard_to_be_free(self):
        order = []
        self.win.settle_clipboard.side_effect = lambda: order.append('settled')
        self.win.gesture = lambda target, attempted: (order.append('keys'), attempted())
        self.paster.paste(self.target, 'Example', Mock())
        self.assertEqual(order, ['settled', 'keys'])

    def test_a_copy_made_during_the_wait_is_not_overwritten(self):
        self.win.settle_clipboard.side_effect = lambda: self.win.write([(13, b'user copied something else')])
        with self.assertRaises(ValueError):
            self.paster.paste(self.target, 'Example', Mock())
        self.assertEqual(self.win.injected, [])

    def test_copy_leaves_the_text_on_the_clipboard(self):
        self.paster.copy('راجع MapView.')
        self.assertEqual(self.win.rows[0][1], 'راجع MapView.'.encode('utf-16-le') + b'\0\0')
        self.win.user.CloseClipboard.assert_called_once()
        self.timer.assert_not_called()      # nothing comes back later to restore the old clipboard
        for text in ('   ', '', None, 'a' * 16001, 'a\0b'):
            with self.assertRaises(ValueError):
                self.paster.copy(text)

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


class ClipboardSettle(unittest.TestCase):
    def windows(self, free):
        win = paste.Windows.__new__(paste.Windows)
        win.user = SimpleNamespace(OpenClipboard=Mock(side_effect=free), CloseClipboard=Mock())
        return win

    def setUp(self):
        for name, value in (('CLIPBOARD_QUIET', .03), ('CLIPBOARD_BUSY_LIMIT', .15)):
            patcher = patch.object(paste, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_returns_once_nobody_has_held_it_for_a_moment(self):
        win = self.windows(lambda _: True)
        started = time.monotonic()
        win.settle_clipboard()
        self.assertGreaterEqual(time.monotonic() - started, .03)
        self.assertLess(time.monotonic() - started, .15)
        self.assertEqual(win.user.CloseClipboard.call_count, win.user.OpenClipboard.call_count)

    def test_another_program_holding_it_restarts_the_wait(self):
        started = time.monotonic()
        busy = [started]

        def free(_):
            if time.monotonic() - started > .06:
                return True
            busy[0] = time.monotonic()
            return False
        win = self.windows(free)
        win.settle_clipboard()
        self.assertGreaterEqual(time.monotonic() - busy[0], .03)
        self.assertGreater(busy[0] - started, .04, 'the holder was seen')

    def test_a_clipboard_that_stays_busy_does_not_block_the_paste(self):
        win = self.windows(lambda _: False)
        started = time.monotonic()
        win.settle_clipboard()
        self.assertGreaterEqual(time.monotonic() - started, .15)
        self.assertLess(time.monotonic() - started, 1)
        win.user.CloseClipboard.assert_not_called()     # never closes what it did not open


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
        entry = desktop.history.entries()['entries'][0]
        self.assertEqual((entry['english'], entry['destinations']), ('Exact prompt', ['Notepad']))

    def test_text_that_cannot_land_goes_to_history_and_the_clipboard(self):
        self.assertEqual(self.api.keep_text({'text': 'راجع الفاتورة.'}, self.token), {'saved': True, 'copied': True})
        self.capture.paster.copy.assert_called_once_with('راجع الفاتورة.')
        entry = desktop.history.entries()['entries'][0]
        self.assertEqual((entry['english'], entry['destinations']), ('راجع الفاتورة.', [desktop.HISTORY_KEPT]))

    def test_keeping_text_reports_what_failed(self):
        self.capture.paster.copy.side_effect = ValueError('busy')
        self.assertEqual(self.api.keep_text({'text': 'Keep me'}, self.token), {'saved': True, 'copied': False})
        self.capture.paster.copy.side_effect = None
        with patch.object(desktop, 'save_history', side_effect=sqlite3.Error('locked')):
            self.assertEqual(self.api.keep_text({'text': 'Keep me'}, self.token), {'saved': False, 'copied': True})

    def test_keeping_text_needs_the_pill_a_token_and_real_text(self):
        with self.assertRaises(ValueError):
            self.api.keep_text({'text': 'Keep me'}, 'bad token')
        with self.assertRaises(ValueError):
            desktop.DesktopApi(self.capture).keep_text({'text': 'Keep me'}, self.token)
        for body in ({'text': '  '}, {'text': None}, {'text': 'a' * 16001}, {'text': 'a\0b'}, 'text'):
            with self.assertRaises(ValueError):
                self.api.keep_text(body, self.token)
        self.capture.paster.copy.assert_not_called()

    def test_show_main_opens_only_a_real_tab(self):
        with patch.object(desktop, 'webview') as view, patch.object(desktop, 'focus_window') as focus:
            self.api.show_main('models', self.token)
            view.windows[0].evaluate_js.assert_called_once_with("window.relayShowTab?.('models')")
            focus.assert_called_once()
            for tab, token in (('models', 'bad token'), ("x');alert('", self.token), ('editor', self.token)):
                with self.assertRaises(ValueError):
                    self.api.show_main(tab, token)
            view.windows[0].evaluate_js.assert_called_once()

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
