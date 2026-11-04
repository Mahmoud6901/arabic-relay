"""Windows foreground-app delivery. A paste gesture never includes Enter."""
import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
import os
import threading
import time


LOCK = threading.Lock()
MAX_CLIPBOARD = 32 * 1024 * 1024
CLIPBOARD_QUIET = .12       # seconds nobody may hold the clipboard before the paste keys go out
CLIPBOARD_BUSY_LIMIT = .6


class POINT(ctypes.Structure):
    _fields_ = [('x', w.LONG), ('y', w.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [('left', w.LONG), ('top', w.LONG), ('right', w.LONG), ('bottom', w.LONG)]


class GUIINFO(ctypes.Structure):
    _fields_ = [('cbSize', w.DWORD), ('flags', w.DWORD),
                *[(name, w.HWND) for name in ('active', 'focus', 'capture', 'menu', 'move', 'caret')],
                ('rect', RECT)]


class KEY(ctypes.Structure):
    _fields_ = [('key', w.WORD), ('scan', w.WORD), ('flags', w.DWORD), ('time', w.DWORD),
                ('extra', ctypes.c_size_t)]


class MOUSE(ctypes.Structure):
    _fields_ = [('dx', w.LONG), ('dy', w.LONG), ('data', w.DWORD), ('flags', w.DWORD),
                ('time', w.DWORD), ('extra', ctypes.c_size_t)]


class INPUTDATA(ctypes.Union):
    _fields_ = [('keyboard', KEY), ('mouse', MOUSE)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', w.DWORD), ('data', INPUTDATA)]


@dataclass(frozen=True)
class Target:
    window: int
    pid: int
    focus: int
    name: str


class Windows:
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'GetForegroundWindow': (w.HWND, []),
            'GetWindowThreadProcessId': (w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
            'GetGUIThreadInfo': (w.BOOL, [w.DWORD, ctypes.POINTER(GUIINFO)]),
            'IsWindow': (w.BOOL, [w.HWND]), 'IsIconic': (w.BOOL, [w.HWND]),
            'ShowWindow': (w.BOOL, [w.HWND, ctypes.c_int]),
            'SetForegroundWindow': (w.BOOL, [w.HWND]),
            'SetFocus': (w.HWND, [w.HWND]),
            'IsChild': (w.BOOL, [w.HWND, w.HWND]),
            'AttachThreadInput': (w.BOOL, [w.DWORD, w.DWORD, w.BOOL]),
            'GetAsyncKeyState': (ctypes.c_short, [ctypes.c_int]),
            'OpenClipboard': (w.BOOL, [w.HWND]), 'CloseClipboard': (w.BOOL, []),
            'EmptyClipboard': (w.BOOL, []), 'EnumClipboardFormats': (w.UINT, [w.UINT]),
            'GetClipboardData': (w.HANDLE, [w.UINT]),
            'SetClipboardData': (w.HANDLE, [w.UINT, w.HANDLE]),
            'GetClipboardSequenceNumber': (w.DWORD, []),
            'RegisterClipboardFormatW': (w.UINT, [w.LPCWSTR]),
            'SendInput': (w.UINT, [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]),
        }
        for name, (result, args) in signatures.items():
            function = getattr(self.user, name)
            function.restype, function.argtypes = result, args
        for name, result, args in (
            ('GlobalSize', ctypes.c_size_t, [w.HGLOBAL]),
            ('GlobalLock', ctypes.c_void_p, [w.HGLOBAL]),
            ('GlobalUnlock', w.BOOL, [w.HGLOBAL]),
            ('GlobalAlloc', w.HGLOBAL, [w.UINT, ctypes.c_size_t]),
            ('GlobalFree', w.HGLOBAL, [w.HGLOBAL]),
            ('OpenProcess', w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
            ('CloseHandle', w.BOOL, [w.HANDLE]),
            ('GetCurrentThreadId', w.DWORD, []),
            ('QueryFullProcessImageNameW', w.BOOL, [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]),
        ):
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = result, args

    def identity(self, window):
        pid = w.DWORD()
        thread = self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        info = GUIINFO(cbSize=ctypes.sizeof(GUIINFO))
        focus = int(info.focus or 0) if self.user.GetGUIThreadInfo(thread, ctypes.byref(info)) else 0
        return pid.value, focus

    def name(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return 'the previous app'
        try:
            text, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
            if self.kernel.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)):
                return text.value.replace('\\', '/').rsplit('/', 1)[-1].removesuffix('.exe')
        finally:
            self.kernel.CloseHandle(handle)
        return 'the previous app'

    def capture(self):
        window = int(self.user.GetForegroundWindow() or 0)
        pid, focus = self.identity(window)
        if not window or not pid or pid == os.getpid():
            return None
        return Target(window, pid, focus, self.name(pid))

    def restore_target(self, target):
        if not self.user.IsWindow(target.window) or self.identity(target.window)[0] != target.pid:
            raise ValueError('The original app closed. Your prompt is preserved; copy it into the intended app.')
        current = self.user.GetForegroundWindow()
        pid, _ = self.identity(current)
        if current != target.window and pid != os.getpid():
            raise ValueError('You switched to another app. Automatic paste stopped; your prompt is ready to copy.')
        if self.user.IsIconic(target.window):
            self.user.ShowWindow(target.window, 9)
        self.user.SetForegroundWindow(target.window)
        deadline = time.monotonic() + .8
        while time.monotonic() < deadline:
            if self.user.GetForegroundWindow() == target.window:
                # Activation and the editor's keyboard focus settle on separate Windows messages.
                if target.focus and self.identity(target.window)[1] != target.focus:
                    self.restore_input(target)
                try:
                    self.check_target(target)
                    return
                except ValueError:
                    pass
            time.sleep(.02)
        raise ValueError('Windows could not restore the original text field. Copy your prompt and paste it manually.')

    def restore_input(self, target):
        if not self.user.IsWindow(target.focus) or not (target.focus == target.window or
                                                       self.user.IsChild(target.window, target.focus)):
            return
        pid = w.DWORD()
        owner = self.user.GetWindowThreadProcessId(target.window, ctypes.byref(pid))
        current = self.kernel.GetCurrentThreadId()
        attached = owner != current and self.user.AttachThreadInput(current, owner, True)
        if owner != current and not attached:
            return
        try:
            if self.user.GetForegroundWindow() == target.window:
                self.user.SetFocus(target.focus)
        finally:
            if attached:
                self.user.AttachThreadInput(current, owner, False)

    def check_target(self, target):
        pid, focus = self.identity(target.window)
        if (self.user.GetForegroundWindow() != target.window or pid != target.pid
                or (target.focus and focus != target.focus)):
            raise ValueError('The original input focus changed. Copy the prompt into the intended text field.')

    def wait_modifiers(self):
        deadline = time.monotonic() + 1.5
        while any(self.user.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            if time.monotonic() >= deadline:
                raise ValueError('Release the keyboard shortcut, then click Paste again. Your text is preserved.')
            time.sleep(.02)

    def open_clipboard(self):
        for _ in range(20):
            if self.user.OpenClipboard(self.user.GetForegroundWindow()):
                return
            time.sleep(.015)
        raise ValueError('The clipboard is busy. Your prompt is preserved; try Copy prompt.')

    def sequence(self):
        return self.user.GetClipboardSequenceNumber()

    def snapshot(self):
        rows, size, fmt = [], 0, 0
        while True:
            fmt = self.user.EnumClipboardFormats(fmt)
            if not fmt:
                return rows
            # Bitmap/metafile/palette handles are not movable global-memory blocks.
            if fmt in (2, 3, 9, 14, 0x80, 0x81, 0x82, 0x83, 0x8E):
                raise ValueError('The current clipboard format cannot be restored safely. Use Copy prompt manually.')
            handle = self.user.GetClipboardData(fmt)
            count = self.kernel.GlobalSize(handle) if handle else 0
            size += count
            if not count or size > MAX_CLIPBOARD:
                raise ValueError('The current clipboard cannot be preserved safely. Use Copy prompt manually.')
            address = self.kernel.GlobalLock(handle)
            if not address:
                raise ValueError('The clipboard could not be preserved. Use Copy prompt manually.')
            try:
                rows.append((fmt, ctypes.string_at(address, count)))
            finally:
                self.kernel.GlobalUnlock(handle)

    def write(self, rows):
        # Allocate all blocks before clearing, so allocation errors preserve the clipboard.
        allocated = []
        try:
            for fmt, data in rows:
                handle = self.kernel.GlobalAlloc(0x42, len(data))
                if not handle:
                    raise ValueError('Not enough memory for the clipboard.')
                allocated.append([fmt, handle])
                address = self.kernel.GlobalLock(handle)
                if not address:
                    raise ValueError('The clipboard could not be prepared.')
                ctypes.memmove(address, data, len(data))
                self.kernel.GlobalUnlock(handle)
            if not self.user.EmptyClipboard():
                raise ValueError('Windows could not update the clipboard.')
            for row in allocated:
                if not self.user.SetClipboardData(*row):
                    raise ValueError('Windows could not update the clipboard.')
                row[1] = None  # Windows now owns this allocation.
        finally:
            for _, handle in allocated:
                if handle:
                    self.kernel.GlobalFree(handle)

    def settle_clipboard(self):
        """Programs that watch the clipboard open it the moment it changes, and most apps silently give up on a paste if
        they find it open. Wait until nobody has held it for a moment. If it stays busy, the paste goes ahead anyway."""
        start = quiet = time.monotonic()
        while time.monotonic() - start < CLIPBOARD_BUSY_LIMIT:
            if self.user.OpenClipboard(None):
                self.user.CloseClipboard()
                if time.monotonic() - quiet >= CLIPBOARD_QUIET:
                    return
            else:
                quiet = time.monotonic()
            time.sleep(.004)

    def put_text(self, text):
        formats = [(13, text.encode('utf-16-le') + b'\0\0')]
        for name in ('CanIncludeInClipboardHistory', 'CanUploadToCloudClipboard'):
            fmt = self.user.RegisterClipboardFormatW(name)
            if fmt:
                formats.append((fmt, b'\0' * 4))
        self.write(formats)

    def gesture(self, target, attempted):
        self.check_target(target)
        keys = ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2))
        events = (INPUT * len(keys))(*(INPUT(type=1, data=INPUTDATA(keyboard=KEY(key=k, flags=f))) for k, f in keys))
        attempted()  # An uncertain/partial input must never be retried automatically.
        count = self.user.SendInput(len(events), events, ctypes.sizeof(INPUT))
        if count != len(events):
            releases = (INPUT * 2)(*(INPUT(type=1, data=INPUTDATA(keyboard=KEY(key=k, flags=2))) for k in (0x56, 0x11)))
            self.user.SendInput(2, releases, ctypes.sizeof(INPUT))
            raise ValueError('Windows did not confirm the full paste shortcut. Check the app before copying again; elevated apps may block input.')


class Paster:
    def __init__(self, backend=None):
        self.backend = backend
        self.pending = False

    def windows(self):
        if self.backend is None:
            self.backend = Windows()
        return self.backend

    def capture(self):
        return self.windows().capture()

    def paste(self, target, text, attempted):
        if not isinstance(text, str) or not text.strip() or len(text) > 16000 or '\0' in text:
            raise ValueError('Prepare a prompt between 1 and 16,000 characters before pasting.')
        if target is None:
            raise ValueError('Click a text field in another app and use Relay’s shortcut to start dictation.')
        if not LOCK.acquire(blocking=False):
            raise ValueError('A paste is already in progress. Your prompt is preserved.')
        snapshot, inserted = None, None
        try:
            win = self.windows()
            win.wait_modifiers()
            win.restore_target(target)
            win.open_clipboard()
            try:
                snapshot = win.snapshot()
                win.put_text(text)
            except Exception:
                if snapshot is not None:
                    win.write(snapshot)
                raise
            finally:
                win.user.CloseClipboard()
            # Windows commits clipboard changes on CloseClipboard, not necessarily on SetClipboardData.
            inserted = win.sequence()
            try:
                if target.name.lower() in ('mstsc', 'msrdc'):
                    time.sleep(.4)
                win.settle_clipboard()
                win.wait_modifiers()
                if win.sequence() != inserted:
                    raise ValueError('The clipboard changed before paste. Your prompt is preserved; copy it manually.')
                win.gesture(target, attempted)
            except Exception:
                self.restore(snapshot, inserted)
                raise
            # Keep the clipboard alive for asynchronous consumers. Restoration never overwrites a newer copy.
            self.pending = True
            timer = threading.Timer(3 if target.name.lower() in ('mstsc', 'msrdc') else 1,
                                    self.restore_and_release, args=(snapshot, inserted))
            timer.daemon = True
            timer.start()
            return {'status': 'paste_requested', 'app': target.name,
                    'message': f'Paste shortcut sent to {target.name}. The message was not submitted.'}
        finally:
            if not self.pending:
                LOCK.release()

    def copy(self, text):
        """Leaves `text` on the clipboard for the user to paste by hand; nothing restores the old clipboard."""
        if not isinstance(text, str) or not text.strip() or len(text) > 16000 or '\0' in text:
            raise ValueError('Prepare a prompt between 1 and 16,000 characters before pasting.')
        win = self.windows()
        win.open_clipboard()
        try:
            win.put_text(text)
        finally:
            win.user.CloseClipboard()

    def restore(self, snapshot, inserted):
        win = self.windows()
        win.open_clipboard()
        try:
            if win.sequence() == inserted:
                win.write(snapshot)
        finally:
            win.user.CloseClipboard()

    def restore_and_release(self, snapshot, inserted):
        try:
            self.restore(snapshot, inserted)
        except Exception:
            pass  # Do not clobber a newer clipboard or log clipboard contents.
        finally:
            self.pending = False
            LOCK.release()
