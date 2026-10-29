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

from . import diagnostics, server, locate, paths, store, t3, updates


class DesktopApi:
    def open_browser_companion(self, token):
        if token != server.CSRF:
            raise ValueError('Invalid request.')
        # A stable folder keeps unpacked extension identity across desktop app updates.
        import shutil
        destination = paths.DATA / 'Browser Companion'
        destination.mkdir(exist_ok=True)
        for source in (paths.ROOT / 'browser_companion').iterdir():
            if source.is_file():
                shutil.copy2(source, destination / source.name)
        os.startfile(str(destination))
        return str(destination)

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
    try:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        webview.create_window('Arabic Relay', server.ORIGIN, js_api=DesktopApi(),
                             width=1380, height=940, min_size=(760, 650),
                             background_color='#000000', text_select=True)
        webview.start(gui='edgechromium', private_mode=False,
                      storage_path=str(paths.DATA / 'NativeWebView'), icon=str(server.ROOT / 'assets/relay.ico'))
    finally:
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
