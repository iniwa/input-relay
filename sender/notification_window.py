"""Non-activating native Windows popup used for clipboard status."""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes

logger = logging.getLogger("clipboard_notification")

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_HIDE = 0
HWND_TOPMOST = -1
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_NCHITTEST = 0x0084
WM_DESTROY = 0x0002
HTTRANSPARENT = -1
DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020
COLOR_WINDOW = 5
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SPI_GETWORKAREA = 0x0030


class _NativePopup:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.hwnd = None
        self.atom = 0
        self.text = ""
        self.class_name = f"InputRelayClipboardNotice_{id(self):x}"
        self._wndproc = None
        self._declare()

    def _declare(self):
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

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32),
            ]

        self._WNDPROC = WNDPROC
        self._WNDCLASSW = WNDCLASSW
        self._PAINTSTRUCT = PAINTSTRUCT
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        self.user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self.user32.UnregisterClassW.restype = wintypes.BOOL
        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.DestroyWindow.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
            wintypes.UINT, wintypes.UINT, wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.TranslateMessage.restype = wintypes.BOOL
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = LRESULT
        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.PostQuitMessage.restype = None
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user32.GetDpiForWindow.restype = wintypes.UINT
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.GetClientRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.FillRect.argtypes = [
            wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH,
        ]
        self.user32.FillRect.restype = ctypes.c_int
        self.user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT,
        ]
        self.user32.SystemParametersInfoW.restype = wintypes.BOOL
        self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user32.GetSystemMetrics.restype = ctypes.c_int
        self.user32.InvalidateRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL,
        ]
        self.user32.InvalidateRect.restype = wintypes.BOOL
        self.user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        self.user32.BeginPaint.restype = wintypes.HDC
        self.user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        self.user32.EndPaint.restype = wintypes.BOOL
        self.user32.DrawTextW.argtypes = [
            wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
            ctypes.POINTER(wintypes.RECT), wintypes.UINT,
        ]
        self.user32.DrawTextW.restype = ctypes.c_int
        self.gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        self.gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        self.gdi32.SetTextColor.restype = wintypes.COLORREF
        self.gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self.gdi32.SetBkMode.restype = ctypes.c_int
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    def start(self):
        instance = self.kernel32.GetModuleHandleW(None)

        @self._WNDPROC
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_NCHITTEST:
                return HTTRANSPARENT
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_PAINT:
                ps = self._PAINTSTRUCT()
                hdc = self.user32.BeginPaint(hwnd, ctypes.byref(ps))
                try:
                    rect = wintypes.RECT()
                    self.user32.GetClientRect(hwnd, ctypes.byref(rect))
                    brush = self.gdi32.CreateSolidBrush(0x002A2A2A)
                    self.user32.FillRect(hdc, ctypes.byref(rect), brush)
                    self.gdi32.DeleteObject(brush)
                    self.gdi32.SetTextColor(hdc, 0x00FFFFFF)
                    self.gdi32.SetBkMode(hdc, 1)
                    self.user32.DrawTextW(
                        hdc, self.text, -1, ctypes.byref(rect),
                        DT_CENTER | DT_VCENTER | DT_SINGLELINE,
                    )
                finally:
                    self.user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0
            if msg == WM_DESTROY:
                self.user32.PostQuitMessage(0)
                return 0
            return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = wndproc
        wc = self._WNDCLASSW()
        wc.lpfnWndProc = wndproc
        wc.hInstance = instance
        wc.hbrBackground = wintypes.HBRUSH(COLOR_WINDOW + 1)
        wc.lpszClassName = self.class_name
        self.atom = self.user32.RegisterClassW(ctypes.byref(wc))
        if not self.atom:
            raise RuntimeError("notification class registration failed")
        ex_style = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
        self.hwnd = self.user32.CreateWindowExW(
            ex_style, self.class_name, "", WS_POPUP,
            0, 0, 420, 64, None, None, instance, None,
        )
        if not self.hwnd:
            self.stop()
            raise RuntimeError("notification window creation failed")

    def pump(self):
        msg = wintypes.MSG()
        while self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))

    def show(self, text):
        self.text = text
        rect = wintypes.RECT()
        if not self.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0,
        ):
            rect = wintypes.RECT(
                0, 0,
                self.user32.GetSystemMetrics(SM_CXSCREEN),
                self.user32.GetSystemMetrics(SM_CYSCREEN),
            )
        dpi = self.user32.GetDpiForWindow(self.hwnd) or 96
        scale = dpi / 96.0
        width, height, margin = (
            round(420 * scale), round(64 * scale), round(20 * scale),
        )
        x = rect.right - width - margin
        y = rect.top + margin
        self.user32.SetWindowPos(
            self.hwnd, wintypes.HWND(HWND_TOPMOST), x, y, width, height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        self.user32.InvalidateRect(self.hwnd, None, True)

    def hide(self):
        if self.hwnd:
            self.user32.ShowWindow(self.hwnd, SW_HIDE)

    def stop(self):
        if self.hwnd:
            self.user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.atom:
            instance = self.kernel32.GetModuleHandleW(None)
            self.user32.UnregisterClassW(self.class_name, instance)
            self.atom = 0
        self._wndproc = None


class NotificationWindow:
    """Thread-safe, latest-only, three-second clipboard notification."""

    def __init__(self, *, backend_factory=_NativePopup, duration=3.0):
        self._backend_factory = backend_factory
        self._duration = duration
        self._queue = queue.Queue(maxsize=1)
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._available = False

    def start(self, timeout=3.0):
        if self._thread is not None and self._thread.is_alive():
            return self._available
        self._ready.clear()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="clipboard-notification",
        )
        self._thread.start()
        self._ready.wait(timeout)
        return self._available

    @property
    def available(self):
        return self._available

    def show(self, text):
        if not self._available:
            return False
        try:
            self._queue.put_nowait(str(text))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(str(text))
        return True

    def shutdown(self, timeout=2.0):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None
        self._available = False

    def pending_count(self):
        return self._queue.qsize()

    def _thread_main(self):
        backend = None
        hide_at = None
        try:
            backend = self._backend_factory()
            backend.start()
            self._available = True
            self._ready.set()
            while not self._stop.is_set():
                backend.pump()
                try:
                    text = self._queue.get(timeout=0.02)
                except queue.Empty:
                    text = None
                if text is not None:
                    backend.show(text)
                    hide_at = time.monotonic() + self._duration
                if hide_at is not None and time.monotonic() >= hide_at:
                    backend.hide()
                    hide_at = None
        except Exception:
            logger.error("clipboard notification unavailable", exc_info=True)
        finally:
            self._available = False
            self._ready.set()
            if backend is not None:
                try:
                    backend.hide()
                    backend.stop()
                except Exception:
                    logger.debug("clipboard notification cleanup failed", exc_info=True)
