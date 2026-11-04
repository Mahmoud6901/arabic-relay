"""One Windows global shortcut, owned and released by its message-loop thread."""
import ctypes
from ctypes import wintypes
import logging
import threading


class RecordingHotkey:
    ID = 0x4152
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

    def _run(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        try:
            if self.stopping.is_set():
                return
            message = wintypes.MSG()
            user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # create the thread's message queue
            self.registered = bool(user.RegisterHotKey(None, self.ID, self.modifiers, self.key))
            if not self.registered:
                self.error = f'{self.label} مش متاح، وممكن تطبيق تاني بيستخدمه. اختار اختصار تاني من الإعدادات.'
                return
            if self.stopping.is_set():
                return
            self.ready.set()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312 and message.wParam == self.ID:
                    # Keep the loop responsive to shutdown while the window loads.
                    threading.Thread(target=self.callback, daemon=True).start()
        except Exception:
            self.error = 'اختصار التسجيل ما اشتغلش. أعد تشغيل البرنامج أو اختار اختصار تاني.'
            logging.exception('Recording shortcut failed')
        finally:
            self.ready.set()
            if self.registered:
                user.UnregisterHotKey(None, self.ID)
                self.registered = False

    def stop(self):
        self.stopping.set()
        if self.thread_id and self.registered:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread:
            self.thread.join(timeout=3)
