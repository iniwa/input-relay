"""Windows CF_UNICODETEXT worker with an injectable backend.

Importing this module doesn't create a window, register a listener, or open
the clipboard.  ``ClipboardWorker.start`` performs all native initialization
on one dedicated daemon thread.  Unit tests supply a fake backend factory.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes

from input_common.clipboard_sync import ProtocolError, validate_text

logger = logging.getLogger("clipboard_win32")

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
WM_CLIPBOARDUPDATE = 0x031D
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
PM_REMOVE = 0x0001
HWND_MESSAGE = -3
MAX_UTF16_BYTES = 128 * 1024 + 2
_OPEN_RETRY_DELAYS = (0.0, 0.02, 0.05, 0.1, 0.2)


class ClipboardUnavailable(RuntimeError):
    pass


class ClipboardBusy(RuntimeError):
    pass


class ClipboardWriteFailed(RuntimeError):
    pass


class ClipboardOperationCancelled(RuntimeError):
    pass


class _Win32Backend:
    """Native backend. Construct and use only on the clipboard thread."""

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.hwnd = None
        self.atom = 0
        self.class_name = f"InputRelayClipboard_{id(self):x}"
        self._wndproc = None
        self._notify = None
        self.should_continue = lambda: True
        self._declare()

    def _declare(self):
        u = self.user32
        k = self.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        self._WNDPROC = WNDPROC
        self._WNDCLASSW = WNDCLASSW
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.RegisterClassW.restype = wintypes.ATOM
        u.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        u.UnregisterClassW.restype = wintypes.BOOL
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.DefWindowProcW.restype = LRESULT
        u.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        u.CreateWindowExW.restype = wintypes.HWND
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.DestroyWindow.restype = wintypes.BOOL
        u.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
            wintypes.UINT, wintypes.UINT, wintypes.UINT,
        ]
        u.PeekMessageW.restype = wintypes.BOOL
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.TranslateMessage.restype = wintypes.BOOL
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = LRESULT
        u.PostQuitMessage.argtypes = [ctypes.c_int]
        u.PostQuitMessage.restype = None
        u.AddClipboardFormatListener.argtypes = [wintypes.HWND]
        u.AddClipboardFormatListener.restype = wintypes.BOOL
        u.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
        u.RemoveClipboardFormatListener.restype = wintypes.BOOL
        u.GetClipboardSequenceNumber.argtypes = []
        u.GetClipboardSequenceNumber.restype = wintypes.DWORD
        u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        u.IsClipboardFormatAvailable.restype = wintypes.BOOL
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.argtypes = []
        u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.argtypes = []
        u.EmptyClipboard.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = [wintypes.UINT]
        u.GetClipboardData.restype = wintypes.HANDLE
        u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u.SetClipboardData.restype = wintypes.HANDLE
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.restype = wintypes.HMODULE
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = wintypes.HGLOBAL
        k.GlobalFree.argtypes = [wintypes.HGLOBAL]
        k.GlobalFree.restype = wintypes.HGLOBAL
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k.GlobalLock.restype = wintypes.LPVOID
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k.GlobalUnlock.restype = wintypes.BOOL
        k.GlobalSize.argtypes = [wintypes.HGLOBAL]
        k.GlobalSize.restype = ctypes.c_size_t

    def start(self, notify):
        self._notify = notify
        instance = self.kernel32.GetModuleHandleW(None)

        @self._WNDPROC
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_CLIPBOARDUPDATE:
                notify()
                return 0
            if msg == WM_DESTROY:
                self.user32.PostQuitMessage(0)
                return 0
            return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = wndproc
        wc = self._WNDCLASSW()
        wc.lpfnWndProc = wndproc
        wc.hInstance = instance
        wc.lpszClassName = self.class_name
        self.atom = self.user32.RegisterClassW(ctypes.byref(wc))
        if not self.atom:
            raise ClipboardUnavailable("window class registration failed")
        self.hwnd = self.user32.CreateWindowExW(
            0, self.class_name, self.class_name, 0, 0, 0, 0, 0,
            wintypes.HWND(HWND_MESSAGE), None, instance, None,
        )
        if not self.hwnd:
            self.stop()
            raise ClipboardUnavailable("clipboard window creation failed")
        if not self.user32.AddClipboardFormatListener(self.hwnd):
            self.stop()
            raise ClipboardUnavailable("clipboard listener registration failed")

    def pump(self):
        msg = wintypes.MSG()
        while self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        if self.hwnd:
            try:
                self.user32.RemoveClipboardFormatListener(self.hwnd)
                self.user32.DestroyWindow(self.hwnd)
            finally:
                self.hwnd = None
        if self.atom:
            instance = self.kernel32.GetModuleHandleW(None)
            self.user32.UnregisterClassW(self.class_name, instance)
            self.atom = 0
        self._wndproc = None

    def sequence(self):
        return int(self.user32.GetClipboardSequenceNumber())

    def _open(self):
        for delay in _OPEN_RETRY_DELAYS:
            if not self.should_continue():
                raise ClipboardOperationCancelled()
            if delay:
                time.sleep(delay)
            if not self.should_continue():
                raise ClipboardOperationCancelled()
            if self.user32.OpenClipboard(self.hwnd):
                return
        raise ClipboardBusy("clipboard remained busy")

    def read_text(self):
        if not self.user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        self._open()
        locked = None
        try:
            if not self.should_continue():
                raise ClipboardOperationCancelled()
            handle = self.user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            size = int(self.kernel32.GlobalSize(handle))
            if size < 2 or size % 2 or size > MAX_UTF16_BYTES:
                raise ProtocolError("invalid CF_UNICODETEXT buffer size")
            locked = self.kernel32.GlobalLock(handle)
            if not locked:
                raise ClipboardUnavailable("GlobalLock failed")
            raw = ctypes.string_at(locked, size)
            terminator = None
            for index in range(0, len(raw) - 1, 2):
                if raw[index:index + 2] == b"\x00\x00":
                    terminator = index
                    break
            if terminator is None:
                raise ProtocolError("invalid CF_UNICODETEXT terminator")
            if terminator == 0:
                return None
            try:
                text = raw[:terminator].decode("utf-16-le")
            except UnicodeDecodeError:
                raise ProtocolError("invalid CF_UNICODETEXT Unicode") from None
            return validate_text(text)
        finally:
            if locked:
                self.kernel32.GlobalUnlock(handle)
            self.user32.CloseClipboard()

    def write_text(self, text):
        validate_text(text)
        raw = text.encode("utf-16-le") + b"\x00\x00"
        if len(raw) > MAX_UTF16_BYTES:
            raise ProtocolError("text exceeds Win32 clipboard buffer limit")
        handle = self.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
        if not handle:
            raise ClipboardWriteFailed("GlobalAlloc failed")
        transferred = False
        locked = self.kernel32.GlobalLock(handle)
        if not locked:
            self.kernel32.GlobalFree(handle)
            raise ClipboardWriteFailed("GlobalLock failed")
        try:
            ctypes.memmove(locked, raw, len(raw))
        finally:
            self.kernel32.GlobalUnlock(handle)
        try:
            self._open()
            try:
                if not self.should_continue():
                    raise ClipboardOperationCancelled()
                if not self.user32.EmptyClipboard():
                    raise ClipboardWriteFailed("EmptyClipboard failed")
                if not self.user32.SetClipboardData(CF_UNICODETEXT, handle):
                    raise ClipboardWriteFailed("SetClipboardData failed")
                transferred = True
                return self.sequence()
            finally:
                self.user32.CloseClipboard()
        finally:
            if not transferred:
                self.kernel32.GlobalFree(handle)


class ClipboardWorker:
    """One bounded command slot and one Win32 clipboard thread."""

    def __init__(
        self,
        on_local_text,
        on_error,
        *,
        backend_factory=_Win32Backend,
        operation_timeout=3.0,
    ):
        self._on_local_text = on_local_text
        self._on_error = on_error
        self._backend_factory = backend_factory
        self._commands = queue.Queue(maxsize=1)
        self._thread = None
        self._watchdog_thread = None
        self._ready = threading.Event()
        self._started_ok = False
        self._stop = threading.Event()
        self._dirty = threading.Event()
        self._lock = threading.Lock()
        self._enabled = False
        self._epoch = None
        self._origin_seq = 0
        self._self_sequence = None
        self._operation_timeout = operation_timeout
        self._operation_started = None
        self._operation_epoch = None
        self._operation_timed_out = False
        self._unavailable = False

    def start(self, timeout=3.0):
        if self._thread is not None and self._thread.is_alive():
            return self._started_ok and not self._unavailable
        self._ready.clear()
        self._stop.clear()
        self._unavailable = False
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="clipboard-win32",
        )
        self._thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_main, daemon=True, name="clipboard-watchdog",
        )
        self._watchdog_thread.start()
        self._ready.wait(timeout)
        return self._started_ok

    def enable(self, epoch):
        ready = threading.Event()
        ready.baselined = False
        with self._lock:
            self._epoch = epoch
            self._enabled = True
            self._origin_seq = 0
            self._self_sequence = None
        self._replace_command(("baseline", epoch, None, ready))
        return ready

    def disable(self):
        with self._lock:
            self._enabled = False
            self._epoch = None
            self._self_sequence = None
        self._clear_commands()
        self._dirty.clear()

    def submit_write(self, epoch, revision, text, *, skip_sequence=None):
        with self._lock:
            if not self._enabled or self._epoch != epoch:
                return False
        self._replace_command(("write", epoch, revision, (text, skip_sequence)))
        return True

    def stop(self, timeout=2.0):
        self.disable()
        self._stop.set()
        self._dirty.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        watchdog = self._watchdog_thread
        if watchdog is not None:
            watchdog.join(timeout)
        clean = thread is None or not thread.is_alive()
        if clean:
            self._thread = None
            self._watchdog_thread = None
        return clean

    def pending_count(self):
        return self._commands.qsize()

    def _replace_command(self, command):
        try:
            self._commands.put_nowait(command)
        except queue.Full:
            try:
                replaced = self._commands.get_nowait()
                if replaced[0] == "baseline" and replaced[3] is not None:
                    replaced[3].set()
            except queue.Empty:
                pass
            self._commands.put_nowait(command)

    def _clear_commands(self):
        while True:
            try:
                command = self._commands.get_nowait()
                if command[0] == "baseline" and command[3] is not None:
                    command[3].set()
            except queue.Empty:
                return

    def _is_current(self, epoch):
        with self._lock:
            return self._enabled and self._epoch == epoch

    def _thread_main(self):
        backend = None
        baseline = 0
        try:
            backend = self._backend_factory()
            if hasattr(backend, "should_continue"):
                backend.should_continue = self._operation_allowed
            backend.start(self._dirty.set)
            self._started_ok = True
            self._ready.set()
            while not self._stop.is_set():
                backend.pump()
                command = None
                try:
                    command = self._commands.get(timeout=0.02)
                except queue.Empty:
                    pass

                if command is not None and command[0] == "baseline":
                    _kind, epoch, _revision, ready = command
                    try:
                        if self._is_current(epoch):
                            baseline = backend.sequence()
                            ready.baselined = True
                    finally:
                        ready.set()
                    continue

                # Observe a pending local update before a remote write can
                # overwrite it; both operations remain on this one thread.
                if self._dirty.is_set():
                    self._dirty.clear()
                    with self._lock:
                        enabled, epoch = self._enabled, self._epoch
                    if enabled:
                        sequence = backend.sequence()
                        if sequence and sequence != baseline:
                            baseline = sequence
                            if sequence == self._self_sequence:
                                self._self_sequence = None
                            else:
                                self._read_and_publish(backend, epoch, sequence)

                if command is None:
                    continue
                kind, epoch, revision, payload = command
                if not self._is_current(epoch):
                    continue
                text, skip_sequence = payload
                if skip_sequence and backend.sequence() == skip_sequence:
                    continue
                try:
                    self._begin_operation(epoch)
                    try:
                        sequence = backend.write_text(text)
                    finally:
                        timed_out = self._end_operation()
                    if timed_out:
                        continue
                    if not self._is_current(epoch):
                        continue
                    if sequence:
                        baseline = sequence
                        self._self_sequence = sequence
                except ClipboardBusy:
                    self._on_error(epoch, "busy")
                except ClipboardOperationCancelled:
                    pass
                except ProtocolError:
                    self._on_error(epoch, "invalid_data")
                except ClipboardWriteFailed:
                    self._on_error(epoch, "write_failed")
        except Exception:
            logger.error("clipboard worker failed", exc_info=True)
            self._on_error(None, "worker_failed")
        finally:
            self._started_ok = False
            self._ready.set()
            if backend is not None:
                try:
                    backend.stop()
                except Exception:
                    logger.debug("clipboard backend cleanup failed", exc_info=True)

    def _read_and_publish(self, backend, epoch, sequence):
        try:
            self._begin_operation(epoch)
            try:
                text = backend.read_text()
            finally:
                timed_out = self._end_operation()
            if timed_out:
                return
        except ClipboardBusy:
            self._on_error(epoch, "busy")
            return
        except ProtocolError as exc:
            code = "too_large" if "exceeds" in str(exc) else "invalid_data"
            self._on_error(epoch, code)
            return
        except ClipboardOperationCancelled:
            return
        except Exception:
            self._on_error(epoch, "worker_failed")
            return
        if text is None or not self._is_current(epoch):
            return
        with self._lock:
            self._origin_seq += 1
            origin_seq = self._origin_seq
        self._on_local_text(epoch, origin_seq, sequence, text)

    def _operation_allowed(self):
        with self._lock:
            epoch = self._operation_epoch
            return (
                not self._stop.is_set()
                and not self._operation_timed_out
                and self._enabled
                and self._epoch == epoch
            )

    def _begin_operation(self, epoch):
        with self._lock:
            self._operation_epoch = epoch
            self._operation_started = time.monotonic()
            self._operation_timed_out = False

    def _end_operation(self):
        with self._lock:
            timed_out = self._operation_timed_out
            self._operation_started = None
            self._operation_epoch = None
            return timed_out

    def _watchdog_main(self):
        while not self._stop.wait(0.05):
            with self._lock:
                started = self._operation_started
                epoch = self._operation_epoch
                fired = self._operation_timed_out
                if (
                    started is not None
                    and not fired
                    and time.monotonic() - started > self._operation_timeout
                ):
                    self._operation_timed_out = True
                    self._unavailable = True
                    notify = True
                else:
                    notify = False
            if notify:
                self._on_error(epoch, "worker_failed")
