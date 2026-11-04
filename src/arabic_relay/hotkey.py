"""Windows global shortcuts, owned and released by their message-loop thread."""
import ctypes
from ctypes import wintypes
import logging
import threading


class RecordingHotkey:
    ID = 0x4152
    STOP = 0x8001
    RELEASE_KEYS = ()
    CHOICES = {
        'ctrl-alt-space': ('Ctrl + Alt + Space', 0x4003, 0x20),
        'ctrl-alt-r': ('Ctrl + Alt + R', 0x4003, 0x52),
        'ctrl-shift-space': ('Ctrl + Shift + Space', 0x4006, 0x20),
    }  # Modifier 0x4000 suppresses key-repeat.

    def __init__(self, callback, choice='ctrl-alt-space'):
        self.callback = callback
        self.choice = choice if choice in self.CHOICES else 'ctrl-alt-space'
        self.label, self.modifiers, self.key = self.CHOICES[self.choice]
        self.thread_id = None
        self.error = None
        self.registered = False
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name='relay-hotkey')
        self.thread.start()
        self.ready.wait(3)

    def bindings(self):
        return {self.ID: (self.modifiers, self.key, self.callback)}

    def _run(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.SetTimer.restype = ctypes.c_size_t
        user.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        user.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        registered = []
        release_timer = 0
        bindings = self.bindings()
        try:
            if self.stopping.is_set():
                return
            message = wintypes.MSG()
            user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # create the thread's message queue
            for identity, (modifiers, key, _) in bindings.items():
                if not user.RegisterHotKey(None, identity, modifiers, key):
                    self.error = f'{self.label} مش متاح، وممكن تطبيق تاني بيستخدمه. اختار اختصار تاني من الإعدادات.'
                    return
                registered.append(identity)
            self.registered = True
            if self.stopping.is_set():
                return
            self.ready.set()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == self.STOP or (message.message == 0x0113 and release_timer and message.wParam == release_timer):
                    if not any(user.GetAsyncKeyState(key) & 0x8000 for key in self.RELEASE_KEYS):
                        break
                    # Keep held Enter/Esc reserved until key-up, so repeats cannot reach the paste target.
                    if not release_timer:
                        release_timer = user.SetTimer(None, 0, 25, None)
                        if not release_timer:
                            break
                if message.message == 0x0312 and message.wParam in bindings and not self.stopping.is_set():
                    # Keep the loop responsive to shutdown while the window loads.
                    threading.Thread(target=bindings[message.wParam][2], daemon=True).start()
        except Exception:
            self.error = 'اختصار التسجيل ما اشتغلش. أعد تشغيل البرنامج أو اختار اختصار تاني.'
            logging.exception('Recording shortcut failed')
        finally:
            self.ready.set()
            if release_timer:
                user.KillTimer(None, release_timer)
            for identity in registered:
                user.UnregisterHotKey(None, identity)
            self.registered = False

    def stop(self):
        self.stopping.set()
        if self.thread_id and self.registered:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.STOP, 0, 0)
        if self.thread:
            self.thread.join(timeout=3)


class CaptureHotkeys(RecordingHotkey):
    """Reserve plain Enter/Esc only while the recording pill exists; other typing keeps its focus."""
    ENTER, ESCAPE = 0x4153, 0x4154
    RELEASE_KEYS = (0x0D, 0x1B)

    def __init__(self, finish, cancel):
        super().__init__(finish)
        self.cancel = cancel
        self.label = 'Enter / Esc'

    def bindings(self):
        return {self.ENTER: (0x4000, 0x0D, self.callback), self.ESCAPE: (0x4000, 0x1B, self.cancel)}
