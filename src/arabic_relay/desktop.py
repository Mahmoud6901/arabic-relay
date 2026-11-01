"""Windows desktop host: WinForms window with embedded WebView2."""
import ctypes
import json
import logging
import os
import sys
import threading
import traceback
import urllib.parse
import webbrowser

os.environ.setdefault('ARABIC_RELAY_PORT', '8791')  # sandbox and dev runs pick another
os.environ['ARABIC_RELAY_NATIVE'] = '1'

import webview

from . import diagnostics, server, locate, paths, speech, store, t3, updates
from .hotkey import RecordingHotkey


class QuickCapture:
    """A separate, temporary editor; it never replaces the main window's draft."""
    def __init__(self, choice='ctrl-alt-space'):
        self.window = None
        self.ready = False
        self.exiting = False
        self.allow_close = False
        self.stage = 'capture'
        self.lock = threading.Lock()
        self.hotkey = RecordingHotkey(self.toggle, choice)

    def toggle(self):
        if self.exiting or not self.lock.acquire(blocking=False):
            return
        try:
            if self.window is None:
                self.ready = False
                self.allow_close = False
                screens = webview.screens
                screen = next((s for s in screens if s.x == 0 and s.y == 0), screens[0])
                # WinForms reports physical bounds; create_window takes DPI-scaled units.
                scale = ctypes.windll.user32.GetDpiForSystem() / 96
                screen_width, screen_height = int(screen.width / scale), int(screen.height / scale)
                self.screen_bounds = (int(screen.x / scale), int(screen.y / scale), screen_width, screen_height)
                self.stage = 'capture'
                width, height = min(420, screen_width - 40), 88
                self.window = webview.create_window(
                    'Arabic Relay — Quick capture', server.ORIGIN + '/?quick=1',
                    js_api=DesktopApi(self, quick=True), width=width, height=height,
                    x=int(screen.x / scale) + (screen_width - width) // 2,
                    y=int(screen.y / scale) + screen_height - height - 70,
                    min_size=(280, 80), frameless=True, easy_drag=False,
                    on_top=True, background_color='#000000', text_select=True)
                self.window.events.closing += self.closing
                self.window.events.closed += self.closed
            elif self.ready:
                self.window.restore()
                self.window.show()
                self.window.evaluate_js('void window.relayQuickToggle()')
        except Exception:
            logging.exception('Quick capture could not open')
        finally:
            self.lock.release()

    def loaded(self):
        with self.lock:
            if self.window and not self.exiting and not self.ready:
                self.ready = True
                self.window.evaluate_js('void window.relayQuickToggle()')

    def set_stage(self, stage):
        with self.lock:
            if not self.window or self.stage == stage:
                return
            self.stage = stage
            x, y, screen_width, screen_height = self.screen_bounds
            width = min(620 if stage == 'review' else 420, screen_width - 40)
            height = min(560, screen_height - 100) if stage == 'review' else 88
            self.window.resize(width, height)
            self.window.move(x + (screen_width - width) // 2, y + screen_height - height - 70)

    def closing(self):
        if self.allow_close or self.exiting:
            return True
        # Closing events run on the UI thread. Query the editor on another thread.
        threading.Thread(target=self.request_close, daemon=True).start()
        return False

    def request_close(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self.window and (not self.ready or self.window.evaluate_js('window.relayQuickClose()')):
                self.allow_close = True
                self.window.destroy()
        finally:
            self.lock.release()

    def closed(self):
        self.window = None
        self.ready = False

    def stop(self):
        self.exiting = True
        self.hotkey.stop()
        if self.window:
            self.window.destroy()


class DesktopApi:
    def __init__(self, capture=None, quick=False):
        self._capture = capture
        self._quick = quick

    def shortcut_status(self, token):
        if token != server.CSRF or not self._capture:
            raise ValueError('Invalid request.')
        key = self._capture.hotkey
        return {'available': key.registered, 'error': key.error, 'choice': key.choice, 'label': key.label}

    def set_shortcut(self, choice, token):
        if token != server.CSRF or not self._capture or choice not in RecordingHotkey.CHOICES:
            raise ValueError('Invalid shortcut.')
        with self._capture.lock:
            self._capture.hotkey.stop()
            self._capture.hotkey = RecordingHotkey(self._capture.toggle, choice)
            self._capture.hotkey.start()
            store.set_setting('capture_shortcut', choice)
        return self.shortcut_status(token)

    def open_quick(self, token):
        if token != server.CSRF or not self._capture:
            raise ValueError('Invalid request.')
        self._capture.toggle()

    def quick_ready(self, token):
        if token != server.CSRF or not self._quick or not self._capture:
            raise ValueError('Invalid request.')
        self._capture.loaded()

    def quick_stage(self, stage, token):
        if token != server.CSRF or not self._quick or not self._capture or stage not in ('capture', 'review'):
            raise ValueError('Invalid request.')
        self._capture.set_stage(stage)

    def close_quick(self, token):
        if token != server.CSRF or not self._quick or not self._capture:
            raise ValueError('Invalid request.')
        self._capture.request_close()

    def open_sign_in(self, url, token):
        if token != server.CSRF or not isinstance(url, str) or len(url) > 5000:
            raise ValueError('Invalid sign-in request.')
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.scheme != 'http' or parsed.netloc != '127.0.0.1:3773' or parsed.path != '/oauth/mcp/authorize':
            raise ValueError('Sign-in must use the local T3 service.')
        with store.AUTH_LOCK:
            pending = t3.OAUTH.get(query.get('state', [''])[0])
            if not pending or query.get('client_id', [''])[0] != pending['client_id']:
                raise ValueError('This sign-in request is no longer active.')
        with locate.external_runtime():
            return webbrowser.open(url)


    def quit(self, token):
        """Restart and update: close the window so the installer can replace the files."""
        if token != server.CSRF:
            raise ValueError('Invalid request.')
        webview.windows[0].destroy()

    def pick_folder(self, token):
        if token != server.CSRF:
            raise ValueError('Invalid folder request.')
        chosen = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        return chosen[0] if chosen else None

    def pick_file(self, token):
        """Browse… for Codex, Claude Code or Wispr Flow: one .exe."""
        if token != server.CSRF:
            raise ValueError('Invalid file request.')
        chosen = webview.windows[0].create_file_dialog(webview.FileDialog.OPEN, file_types=('Programs (*.exe)',))
        return chosen[0] if chosen else None


def run():
    with store.database():
        pass
    if getattr(sys, 'frozen', False):  # a source checkout never replaces itself
        updates.start_background()
    httpd = server.bind(server.PORT)
    (paths.DATA / 'instance.json').write_text(json.dumps({'pid': os.getpid(), 'port': httpd.server_address[1]}), encoding='utf-8')
    capture = QuickCapture(store.get_setting('capture_shortcut', 'ctrl-alt-space'))
    try:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        window = webview.create_window('Arabic Relay', server.ORIGIN, js_api=DesktopApi(capture),
                             width=1380, height=940, min_size=(760, 650),
                             background_color='#000000', text_select=True)
        window.events.closed += capture.stop
        webview.start(capture.hotkey.start, gui='edgechromium', private_mode=False,
                      storage_path=str(paths.DATA / 'NativeWebView'), icon=str(server.ROOT / 'assets/relay.ico'))
    finally:
        capture.stop()
        speech.cancel_preparation()
        httpd.shutdown()
        httpd.server_close()
        updates.install_on_quit()


MUTEX = None


def single_instance(name=r'Local\ArabicRelay.Window'):
    """True for the first window of this Windows user; the handle lives as long as the process."""
    global MUTEX
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return False
    MUTEX = handle
    return True


def focus_window(pid):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            title = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 64)
            if title.value == 'Arabic Relay':
                found.append(hwnd)
                return False
        return True
    user32.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = ctypes.c_void_p(found[0])
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def focus_existing():
    """A second launch brings the first window forward; a half-written instance file just means "not yet"."""
    try:
        pid = json.loads((paths.DATA / 'instance.json').read_text(encoding='utf-8'))['pid']
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return type(pid) is int and focus_window(pid)


def main():
    diagnostics.start_logging()
    if not single_instance():
        focus_existing()
        return
    try:
        run()
    except Exception as error:
        logging.exception('Desktop startup failed')
        diagnostics.record_crash(traceback.format_exc())
        ctypes.windll.user32.MessageBoxW(None, str(error), 'Arabic Relay', 0x10)
        sys.exit(1)
