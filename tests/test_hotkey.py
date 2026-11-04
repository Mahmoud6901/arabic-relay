"""Shortcut lifecycle and native popup dispatch without touching the real desktop."""
import unittest
from unittest.mock import MagicMock, patch, call
from arabic_relay import desktop, hotkey, server


class Shortcut(unittest.TestCase):
    def test_capture_keys_register_only_plain_enter_and_escape_and_release_both(self):
        user = MagicMock()
        user.GetMessageW.return_value = 0
        keys = hotkey.CaptureHotkeys(MagicMock(), MagicMock())
        with patch.object(hotkey.ctypes, 'WinDLL', return_value=user):
            keys._run()
        self.assertEqual(user.RegisterHotKey.call_args_list, [
            call(None, keys.ENTER, 0x4000, 0x0D), call(None, keys.ESCAPE, 0x4000, 0x1B)])
        self.assertEqual(user.UnregisterHotKey.call_args_list, [call(None, keys.ENTER), call(None, keys.ESCAPE)])

    def test_partial_capture_key_registration_is_cleaned_up(self):
        user = MagicMock()
        user.RegisterHotKey.side_effect = [True, False]
        keys = hotkey.CaptureHotkeys(MagicMock(), MagicMock())
        with patch.object(hotkey.ctypes, 'WinDLL', return_value=user):
            keys._run()
        self.assertFalse(keys.registered)
        user.UnregisterHotKey.assert_called_once_with(None, keys.ENTER)

    def test_capture_keys_dispatch_the_matching_actions(self):
        user = MagicMock()
        finish, cancel = MagicMock(), MagicMock()
        keys = hotkey.CaptureHotkeys(finish, cancel)
        messages = iter([keys.ENTER, keys.ESCAPE, 12345])
        def next_message(pointer, *_):
            identity = next(messages, None)
            if identity is None:
                return 0
            pointer._obj.message, pointer._obj.wParam = 0x0312, identity
            return 1
        user.GetMessageW.side_effect = next_message
        with patch.object(hotkey.ctypes, 'WinDLL', return_value=user), patch.object(hotkey.threading, 'Thread') as thread:
            keys._run()
        self.assertEqual(thread.call_args_list, [call(target=finish, daemon=True), call(target=cancel, daemon=True)])

    def test_capture_close_waits_for_key_up_before_returning_enter_to_target(self):
        user = MagicMock()
        user.SetTimer.return_value = 42
        user.GetAsyncKeyState.side_effect = [0x8000, 0, 0]
        keys = hotkey.CaptureHotkeys(MagicMock(), MagicMock())
        messages = iter([(keys.STOP, 0), (0x0113, 42)])
        def next_message(pointer, *_):
            item = next(messages, None)
            if item is None:
                return 0
            pointer._obj.message, pointer._obj.wParam = item
            return 1
        user.GetMessageW.side_effect = next_message
        with patch.object(hotkey.ctypes, 'WinDLL', return_value=user):
            keys._run()
        user.SetTimer.assert_called_once_with(None, 0, 25, None)
        user.KillTimer.assert_called_once_with(None, 42)
        self.assertEqual(user.UnregisterHotKey.call_count, 2)

    def test_finish_does_not_toggle_or_reopen_an_old_capture(self):
        capture = desktop.QuickCapture()
        window = MagicMock()
        capture.window, capture.ready = window, True
        capture.finish(window)
        window.evaluate_js.assert_called_once_with('void window.relayQuickFinish()')
        window.reset_mock()
        capture.window = MagicMock()
        capture.finish(window)
        window.evaluate_js.assert_not_called()

    def test_conflict_is_visible_and_does_not_unregister_another_app(self):
        user = MagicMock()
        user.RegisterHotKey.return_value = False
        key = hotkey.RecordingHotkey(MagicMock())
        with patch.object(hotkey.ctypes, 'WinDLL', return_value=user):
            key._run()
        self.assertFalse(key.registered)
        self.assertIn('مش متاح', key.error)
        user.UnregisterHotKey.assert_not_called()
        self.assertTrue(key.ready.is_set())

    def test_quit_releases_registered_combination(self):
        user = MagicMock()
        user.GetMessageW.return_value = 0
        key = hotkey.RecordingHotkey(MagicMock())
        with patch.object(hotkey.ctypes, 'WinDLL', return_value=user):
            key._run()
        user.RegisterHotKey.assert_called_once_with(None, key.ID, 0x4003, 0x20)
        user.UnregisterHotKey.assert_called_once_with(None, key.ID)
        self.assertFalse(key.registered)

    def test_popup_waits_for_page_ready_and_reuses_one_editor(self):
        capture = desktop.QuickCapture()
        window = MagicMock()
        with patch.object(desktop, 'webview') as view, patch.object(desktop.ctypes, 'windll') as dll, patch.object(desktop, 'see_through'), patch.object(desktop, 'CaptureHotkeys') as keys:
            dll.user32.GetDpiForSystem.return_value = 144
            view.screens = [MagicMock(width=1920, height=1080, x=-1920, y=0),
                            MagicMock(width=1200, height=900, x=0, y=0)]
            create = view.create_window
            create.return_value = window
            capture.toggle()
            capture.toggle()  # second press during initial loading does not duplicate
            create.assert_called_once()
            self.assertEqual(create.call_args.kwargs['height'], 56)
            self.assertEqual(create.call_args.kwargs['min_size'], (120, 40))
            self.assertEqual(create.call_args.kwargs['y'], 490)
            self.assertTrue(create.call_args.kwargs['frameless'])
            self.assertFalse(create.call_args.kwargs['focus'])    # the user's app keeps the keyboard
            self.assertIsNone(window.native.Activate())           # pywebview would otherwise activate it anyway
            capture.set_stage('message')
            window.resize.assert_called_once_with(400, 60)
            window.move.assert_called_once_with(200, 470)
            capture.set_stage('message')
            window.resize.assert_called_once()
            window.evaluate_js.assert_not_called()
            capture.loaded()
            capture.loaded()  # bridge + initial refresh may both report ready
            keys.assert_called_once()
            keys.return_value.start.assert_called_once()
            window.evaluate_js.assert_called_once_with('void window.relayQuickToggle()')
            capture.toggle()
            self.assertEqual(window.evaluate_js.call_count, 2)
            create.assert_called_once()
            window.show.assert_called_once()
        capture.closed()
        keys.return_value.stop.assert_called_once()
        self.assertIsNone(capture.window)
        self.assertFalse(capture.ready)

    def test_close_refusal_keeps_draft_and_shutdown_stops_shortcut(self):
        capture = desktop.QuickCapture()
        capture.window = MagicMock()
        capture.ready = True
        capture.window.evaluate_js.return_value = False
        capture.request_close()
        capture.window.destroy.assert_not_called()
        capture.window.evaluate_js.return_value = True
        capture.request_close()
        capture.window.destroy.assert_called_once()
        with patch.object(capture.hotkey, 'stop') as stop:
            capture.stop()
            stop.assert_called_once()

    def test_only_the_pill_can_ask_for_the_keyboard(self):
        capture = MagicMock()
        with patch.object(desktop, 'take_keyboard') as take:
            for api, token in ((desktop.DesktopApi(capture), server.CSRF), (desktop.DesktopApi(capture, quick=True), 'wrong')):
                with self.assertRaises(ValueError):
                    api.quick_keyboard(token)
            take.assert_not_called()
            desktop.DesktopApi(capture, quick=True).quick_keyboard(server.CSRF)
            take.assert_called_once_with(capture.window)

    def test_native_shortcut_methods_require_token_and_popup_role(self):
        capture = MagicMock()
        api = desktop.DesktopApi(capture)
        for method in (api.open_quick, api.shortcut_status, api.quick_ready):
            with self.assertRaises(ValueError):
                method('wrong')
        with self.assertRaises(ValueError):
            api.quick_ready(server.CSRF)
        desktop.DesktopApi(capture, quick=True).quick_ready(server.CSRF)
        capture.loaded.assert_called_once()
        with self.assertRaises(ValueError):
            api.quick_stage('message', server.CSRF)
        with self.assertRaises(ValueError):
            desktop.DesktopApi(capture, quick=True).quick_stage('invalid', server.CSRF)
        with self.assertRaises(ValueError):
            api.close_quick(server.CSRF)
        desktop.DesktopApi(capture, quick=True).quick_stage('message', server.CSRF)
        capture.set_stage.assert_called_once_with('message')


if __name__ == '__main__':
    unittest.main()
