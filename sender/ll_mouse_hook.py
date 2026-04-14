"""
Ultra-light WH_MOUSE_LL hook for reliable mouse-button suppression.

pynput の suppress=True でも block できるが、コールバックが Python 側で重い処理
（queue 投入や WS 送信の同期点）を挟むと LowLevelHooksTimeout（既定 ~300ms）で
フックが外され、以降のイベントが素通りする。

このモジュールは「remote フラグを見て即 return 1 するだけ」のネイティブ相当
コールバックを ctypes で仕掛け、タイムアウト耐性を最大化する。pynput と併用
しても LIFO で両方が呼ばれるため二重防護になる。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_MOUSE_LL = 14
WM_QUIT = 0x0012

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E

_BUTTON_MSGS = frozenset({
    WM_LBUTTONDOWN, WM_LBUTTONUP,
    WM_RBUTTONDOWN, WM_RBUTTONUP,
    WM_MBUTTONDOWN, WM_MBUTTONUP,
    WM_XBUTTONDOWN, WM_XBUTTONUP,
    WM_MOUSEWHEEL, WM_MOUSEHWHEEL,
})

LRESULT = ctypes.c_ssize_t
LowLevelMouseProc = ctypes.WINFUNCTYPE(
    LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
)

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, LowLevelMouseProc, ctypes.c_void_p, wintypes.DWORD,
]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class LowLevelMouseBlocker:
    """グローバルなマウスボタン遮断フック。remote mode 中のみ有効化する。"""

    def __init__(self):
        self._suppress = False
        self._hook = None
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._proc_ref = None  # keep C callback alive

    def set_suppress(self, enabled: bool):
        self._suppress = bool(enabled)

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ll-mouse-hook",
        )
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def stop(self):
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = None
        self._hook = None

    def _run(self):
        suppress_ref = self  # closure

        @LowLevelMouseProc
        def _proc(nCode, wParam, lParam):
            # nCode < 0 は素通し必須（MSDN 要件）
            if nCode == 0 and suppress_ref._suppress and wParam in _BUTTON_MSGS:
                return 1  # イベントを食う
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._proc_ref = _proc
        hmod = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, _proc, hmod, 0)
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ready.set()
        if not self._hook:
            print("[LLMouseHook] SetWindowsHookExW failed")
            return
        try:
            msg = wintypes.MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
