"""Checks native window ownership and restricts the sign-in bridge. No T3 writes."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from arabic_relay import desktop


class DesktopLifecycle(unittest.TestCase):
    def test_native_host_owns_window_and_server(self):
        httpd = MagicMock(server_address=('127.0.0.1', 8791))
        with tempfile.TemporaryDirectory() as folder, patch.object(desktop.paths, 'DATA', Path(folder)), \
                patch.object(desktop.server, 'bind', return_value=httpd) as bind, \
                patch.object(desktop.webview, 'create_window') as create, patch.object(desktop.webview, 'start') as start:
            desktop.run()
            instance = json.loads((Path(folder) / 'instance.json').read_text(encoding='utf-8'))
        bind.assert_called_once_with(desktop.server.PORT)
        self.assertEqual(instance, {'pid': os.getpid(), 'port': 8791})
        self.assertEqual(create.call_args.args, ('Arabic Relay', desktop.server.ORIGIN))
        self.assertEqual(start.call_args.kwargs['gui'], 'edgechromium')
        self.assertFalse(start.call_args.kwargs['private_mode'])
        httpd.shutdown.assert_called_once()
        httpd.server_close.assert_called_once()

    def test_session_sign_in_bridge_is_removed(self):
        with patch.object(desktop.webbrowser, 'open') as opened:
            with self.assertRaisesRegex(ValueError, 'removed'):
                desktop.DesktopApi().open_sign_in('http://127.0.0.1:3773/oauth/mcp/authorize', desktop.server.CSRF)
            opened.assert_not_called()


class Updates(unittest.TestCase):
    def test_quit_checks_the_token(self):
        window = MagicMock()
        with patch.object(desktop.webview, 'windows', [window]):
            with self.assertRaises(ValueError):
                desktop.DesktopApi().quit('wrong-token')
            window.destroy.assert_not_called()
            desktop.DesktopApi().quit(desktop.server.CSRF)
        window.destroy.assert_called_once()

    def test_updates_install_after_the_window_closes(self):
        order = []
        httpd = MagicMock(server_address=('127.0.0.1', 8791))
        httpd.shutdown.side_effect = lambda: order.append('shutdown')
        with tempfile.TemporaryDirectory() as folder, patch.object(desktop.paths, 'DATA', Path(folder)), \
                patch.object(desktop.server, 'bind', return_value=httpd), patch.object(desktop.webview, 'create_window'), \
                patch.object(desktop.webview, 'start'), \
                patch.object(desktop.updates, 'install_on_quit', side_effect=lambda: order.append('install')), \
                patch.object(desktop.updates, 'start_background') as background:
            desktop.run()
            background.assert_not_called()  # only the installed (frozen) app updates itself
            with patch.object(desktop.sys, 'frozen', True, create=True):
                desktop.run()
            background.assert_called_once()
        self.assertEqual(order, ['shutdown', 'install', 'shutdown', 'install'])


class FilePickers(unittest.TestCase):
    def test_file_pickers_check_token_and_use_file_dialog(self):
        window = MagicMock()
        window.create_file_dialog.return_value = ('C:\\Tools\\codex.exe',)
        with patch.object(desktop.webview, 'windows', [window]):
            with self.assertRaises(ValueError):
                desktop.DesktopApi().pick_file('wrong-token')
            with self.assertRaises(ValueError):
                desktop.DesktopApi().pick_folder('wrong-token')
            self.assertEqual(desktop.DesktopApi().pick_file(desktop.server.CSRF), 'C:\\Tools\\codex.exe')
            self.assertEqual(window.create_file_dialog.call_args.args[0], desktop.webview.FileDialog.OPEN)
            self.assertEqual(window.create_file_dialog.call_args.kwargs['file_types'], ('Programs (*.exe)',))
            desktop.DesktopApi().pick_folder(desktop.server.CSRF)
            self.assertEqual(window.create_file_dialog.call_args.args[0], desktop.webview.FileDialog.FOLDER)
            window.create_file_dialog.return_value = None
            self.assertIsNone(desktop.DesktopApi().pick_file(desktop.server.CSRF))


if __name__ == '__main__':
    unittest.main()
