"""Windows desktop host: WinForms window with embedded WebView2."""
import ctypes
import json
import logging
import os
import sys
import threading
import traceback
import urllib.parse
import secrets
import sqlite3
import webbrowser

os.environ.setdefault('ARABIC_RELAY_PORT', '8791')  # sandbox and dev runs pick another
os.environ['ARABIC_RELAY_NATIVE'] = '1'

import webview

from . import diagnostics, server, locate, paths, speech, store, t3, updates, paste, history, inline
from .hotkey import RecordingHotkey, CaptureHotkeys


PILL = {'capture': (147, 40), 'message': (400, 60)}   # logical pixels; the page's pill fills the window exactly
FRAME = 16      # WinForms takes this much off a frameless window's requested size
HISTORY_KEPT = 'اتنسخ، ما اتلصقش'


def see_through(window):
    """Windows paints a default backdrop behind WebView2's transparent page. Turning it off and extending the frame over
    the whole window lets the page's own alpha reach the desktop, so the pill has smooth edges and nothing around it.
    If any of this fails, a grey box shows behind the pill and recording still works."""
    try:
        import System
        from System.Drawing import Color
        native = window.native

        def apply():
            handle = ctypes.c_void_p(native.Handle.ToInt64())
            dwm = ctypes.windll.dwmapi
            native.BackColor = Color.Black      # black reads as fully see-through once the frame covers the window
            dwm.DwmExtendFrameIntoClientArea(handle, ctypes.byref((ctypes.c_int * 4)(-1, -1, -1, -1)))
            dwm.DwmSetWindowAttribute(handle, 38, ctypes.byref(ctypes.c_int(1)), 4)   # backdrop type: none
            native.Invalidate()
        native.Invoke(System.Action(apply))
    except Exception:
        logging.exception('The recording pill could not be made see-through')


def leave_keyboard_alone(window):
    """The pill shows while the user's app stays in front, so keys typed during a recording still reach that app.
    pywebview activates a transparent window whatever its focus option says, so that one call is switched off here.
    If this fails, the pill takes the keyboard while it is open and the paste still gives it back."""
    try:
        window.native.Activate = lambda: None
    except Exception:
        logging.exception('The recording pill could not leave the keyboard alone')


def take_keyboard(window):
    """Wispr Flow types into the pill, so with Flow alone the pill needs the keyboard."""
    import System
    native = window.native

    def apply():
        window.focus = True     # until this changes, pywebview puts no-activate back on every activation
        del native.Activate
        user32 = ctypes.windll.user32
        handle = native.Handle.ToInt32()
        user32.SetWindowLongW(handle, -20, user32.GetWindowLongW(handle, -20) & ~0x08000000)
        native.Activate()
    native.Invoke(System.Action(apply))


def save_history(entry_id, english, destination):
    with store.database() as db:
        history.initialize(db)
        history.record(db, entry_id, {'english': english, 'targets': [{'title': destination}]})


def history_changed():
    """The main window lists History. Refreshing it runs apart from the paste, so it can never delay or fail one."""
    def tell():
        try:
            webview.windows[0].evaluate_js('window.relayHistoryChanged?.()')
        except Exception:
            logging.debug('The main window did not refresh History', exc_info=True)
    threading.Thread(target=tell, daemon=True).start()


class QuickCapture:
    """The recording pill: a small temporary window that exists from the shortcut until the words are delivered."""
    def __init__(self, choice='ctrl-alt-space'):
        self.window = None
        self.ready = False
        self.exiting = False
        self.allow_close = False
        self.stage = 'capture'
        self.lock = threading.Lock()
        self.paster = paste.Paster()
        self.target = None
        self.next_target = None
        self.capture_id = None
        self.paste_attempted = False
        self.hotkey = RecordingHotkey(self.toggle, choice)
        self.capture_keys = None

    def toggle(self):
        if self.exiting or not self.lock.acquire(blocking=False):
            return
        try:
            candidate = self.paster.capture()
            if candidate is not None:
                self.next_target = candidate
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
                width, height = PILL['capture']
                self.window = webview.create_window(
                    'Arabic Relay', server.ORIGIN + '/?quick=1',
                    js_api=DesktopApi(self, quick=True), width=width + FRAME, height=height + FRAME,
                    x=int(screen.x / scale) + (screen_width - width) // 2,
                    y=int(screen.y / scale) + screen_height - height - 70,
                    min_size=(120, 40), frameless=True, easy_drag=False,
                    on_top=True, focus=False, transparent=True, shadow=False, background_color='#000000')
                see_through(self.window)    # the form exists by now, and shows only once its page starts loading
                leave_keyboard_alone(self.window)
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
                self.place()    # WinForms' take-off for a frameless window depends on the display scale
                window = self.window
                self.capture_keys = CaptureHotkeys(lambda: self.finish(window), lambda: self.request_close(window))
                self.capture_keys.start()
                if not self.capture_keys.registered:
                    logging.warning('Enter/Esc could not be reserved: %s', self.capture_keys.error)
                self.window.evaluate_js('void window.relayQuickToggle()')

    def finish(self, window):
        with self.lock:
            if self.window is window and self.ready and not self.exiting:
                window.evaluate_js('void window.relayQuickFinish()')

    def place(self):
        """Exact size, centred above the bottom of the screen."""
        x, y, screen_width, screen_height = self.screen_bounds
        width, height = PILL[self.stage]
        width = min(width, screen_width - 40)
        self.window.resize(width, height)
        self.window.move(x + (screen_width - width) // 2, y + screen_height - height - 70)

    def set_stage(self, stage):
        with self.lock:
            if not self.window or self.stage == stage:
                return
            self.stage = stage
            self.place()

    def closing(self):
        if self.allow_close or self.exiting:
            return True
        # Closing events run on the UI thread. Query the editor on another thread.
        threading.Thread(target=self.request_close, daemon=True).start()
        return False

    def request_close(self, expected_window=None):
        if not self.lock.acquire(blocking=False):
            return
        try:
            if expected_window is not None and self.window is not expected_window:
                return
            if self.window and (not self.ready or self.window.evaluate_js('window.relayQuickClose()')):
                self.allow_close = True
                self.window.destroy()
        finally:
            self.lock.release()

    def closed(self):
        if self.capture_keys:
            self.capture_keys.stop()
            self.capture_keys = None
        self.window = None
        self.ready = False
        self.target = self.next_target = None
        self.capture_id = None

    def stop(self):
        self.exiting = True
        self.hotkey.stop()
        if self.capture_keys:
            self.capture_keys.stop()
        if self.window:
            self.window.destroy()


class DesktopApi:
    def __init__(self, capture=None, quick=False):
        self._capture = capture
        self._quick = quick

    def begin_capture(self, token):
        if token != server.CSRF or not self._quick or not self._capture:
            raise ValueError('Invalid recording request.')
        capture = self._capture
        with capture.lock:
            capture.target = capture.next_target or capture.target
            capture.next_target = None
            capture.capture_id = secrets.token_urlsafe(24)
            capture.paste_attempted = False
            return {'captureId': capture.capture_id, 'available': capture.target is not None,
                    'app': capture.target.name if capture.target else None}

    def paste_to_target(self, body, token):
        if token != server.CSRF or not self._quick or not self._capture or not isinstance(body, dict):
            raise ValueError('Invalid paste request.')
        capture = self._capture
        with capture.lock:
            if not capture.capture_id or body.get('captureId') != capture.capture_id or capture.paste_attempted:
                raise ValueError('This recording already attempted paste or expired. Check the target before copying again.')
            if body.get('mode') == 'inline':
                inline.verify_send(body)
            target = capture.target
            def attempted():
                capture.paste_attempted = True
            result = capture.paster.paste(target, body.get('english'), attempted)
            try:
                save_history(capture.capture_id, body['english'], target.name)
            except sqlite3.Error:
                result['message'] += ' History could not be saved; do not paste again just to save history.'
        history_changed()
        return result

    def keep_text(self, body, token):
        """The paste could not land: the words stay in History and on the clipboard, and the pill says where."""
        if token != server.CSRF or not self._quick or not self._capture or not isinstance(body, dict):
            raise ValueError('Invalid paste request.')
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 16000 or '\0' in text:
            raise ValueError('Invalid paste request.')
        capture = self._capture
        saved = copied = True
        with capture.lock:
            entry = capture.capture_id or secrets.token_urlsafe(24)
            try:
                save_history(entry, text, HISTORY_KEPT)
            except sqlite3.Error:
                logging.exception('Kept text could not be saved to History')
                saved = False
            try:
                capture.paster.copy(text)
            except (ValueError, OSError):
                logging.exception('Kept text could not be copied')
                copied = False
        history_changed()
        return {'saved': saved, 'copied': copied}

    def show_main(self, tab, token):
        """The recording cannot start without a setting: show the place to fix it."""
        if token != server.CSRF or not self._capture or tab not in ('history', 'models', 'settings'):
            raise ValueError('Invalid request.')
        webview.windows[0].evaluate_js(f"window.relayShowTab?.('{tab}')")
        focus_window(os.getpid())

    def finish_paste(self, capture_id, token):
        if token != server.CSRF or not self._quick or not self._capture:
            raise ValueError('Invalid paste request.')
        capture = self._capture
        if capture_id == capture.capture_id and capture.paste_attempted and capture.window:
            capture.allow_close = True
            capture.window.destroy()

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

    def quick_keyboard(self, token):
        if token != server.CSRF or not self._quick or not self._capture:
            raise ValueError('Invalid request.')
        window = self._capture.window
        if window:
            # Flow needs normal keyboard/IME input; the focused page handles Enter/Esc itself.
            if self._capture.capture_keys:
                self._capture.capture_keys.stop()
            take_keyboard(window)

    def quick_stage(self, stage, token):
        if token != server.CSRF or not self._quick or not self._capture or stage not in PILL:
            raise ValueError('Invalid request.')
        self._capture.set_stage(stage)

    def close_quick(self, token):
        if token != server.CSRF or not self._quick or not self._capture:
            raise ValueError('Invalid request.')
        self._capture.request_close()

    def open_sign_in(self, url, token):
        raise ValueError('Session connections have been removed. Start dictation from the app where you want to paste.')


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
                             width=900, height=680, min_size=(640, 520),
                             background_color='#08090a', text_select=True)
        window.events.closed += capture.stop
        window.events.loaded += speech.prewarm   # only when the user asked for it; never blocks the window
        webview.start(capture.hotkey.start, gui='edgechromium', private_mode=False,
                      storage_path=str(paths.DATA / 'NativeWebView'), icon=str(server.ROOT / 'assets/relay.ico'))
    finally:
        capture.stop()
        speech.cancel_preparation()
        speech.shutdown_engine()
        httpd.shutdown()
        httpd.server_close()
        updates.install_on_quit()


MUTEX = None


def instance_name():
    """One window per data folder: the default folder keeps the original name; a separate development or
    verification folder (ARABIC_RELAY_DATA_DIR) gets its own, so it never focuses or replaces the user's app."""
    if not os.environ.get('ARABIC_RELAY_DATA_DIR'):
        return r'Local\ArabicRelay.Window'
    import hashlib
    return r'Local\ArabicRelay.Window-' + hashlib.sha256(str(paths.DATA.resolve()).lower().encode()).hexdigest()[:16]


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
    if not single_instance(instance_name()):
        focus_existing()
        return
    try:
        run()
    except Exception as error:
        logging.exception('Desktop startup failed')
        diagnostics.record_crash(traceback.format_exc())
        ctypes.windll.user32.MessageBoxW(None, str(error), 'Arabic Relay', 0x10)
        sys.exit(1)
