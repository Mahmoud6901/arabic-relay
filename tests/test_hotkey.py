"""Shortcut lifecycle and native popup dispatch without touching the real desktop."""
import unittest
from unittest.mock import MagicMock, patch
from arabic_relay import desktop, hotkey, server


class Shortcut(unittest.TestCase):
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
        with patch.object(desktop, 'webview') as view, patch.object(desktop.ctypes, 'windll') as dll, patch.object(desktop, 'see_through'):
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
            window.evaluate_js.assert_called_once_with('void window.relayQuickToggle()')
            capture.toggle()
            self.assertEqual(window.evaluate_js.call_count, 2)
            create.assert_called_once()
            window.show.assert_called_once()
        capture.closed()
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
