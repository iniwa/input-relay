"""
Windows Raw Input API によるマウスデルタ取得。

ゲーム内でカーソルが画面中央に固定されているケースでも Raw Input なら生の
移動量を取得できる。60Hz で累積デルタを flush する。flush は Windows の
SetTimer で発火する WM_TIMER に合わせることで、polling 待ちのジッタを減らす。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes, WINFUNCTYPE, POINTER, byref, sizeof


_WM_INPUT = 0x00FF
_WM_TIMER = 0x0113
_RID_INPUT = 0x10000003
_RIM_TYPEMOUSE = 0
_RIDEV_INPUTSINK = 0x00000100
_MOUSE_MOVE_ABSOLUTE = 0x01
_FLUSH_TIMER_ID = 1
_FLUSH_INTERVAL_MS = 16  # ~60Hz

_WNDPROC_TYPE = WINFUNCTYPE(
    ctypes.c_long, wintypes.HWND, wintypes.UINT,
    wintypes.WPARAM, wintypes.LPARAM,
)


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _ButtonsUnion(ctypes.Union):
    class _S(ctypes.Structure):
        _fields_ = [
            ("usButtonFlags", wintypes.USHORT),
            ("usButtonData", ctypes.c_short),
        ]
    _fields_ = [("ulButtons", wintypes.ULONG), ("s", _S)]


class _RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _ButtonsUnion),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class _RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", _RAWINPUTHEADER),
        ("mouse", _RAWMOUSE),
    ]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC_TYPE),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


def run(is_running, on_delta):
    """Run the raw mouse message loop.

    is_running: callable() -> bool. The loop exits when it returns False.
    on_delta:   callable(dx: int, dy: int) -> None. Called on each flush with
                the accumulated delta over ~16ms. 全ての raw 事象は失われず
                蓄積されるため、遅延はあっても精度は落ちない。
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # GetRawInputData の引数型を明示 (Python 3.12+ で厳格化)
    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
        POINTER(wintypes.UINT), wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT

    # 64bit ハンドルを扱う呼び出しに restype/argtypes を明示。
    # ll_mouse_hook など他モジュールが kernel32 のプロキシを共有して
    # restype を変更するため、ここでも明示しないと argument 11
    # (hInstance) で OverflowError が発生する。
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.DefWindowProcW.restype = ctypes.c_long
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.DispatchMessageW.restype = ctypes.c_long
    user32.DispatchMessageW.argtypes = [POINTER(wintypes.MSG)]

    # Windows デフォルトタイマー分解能は ~15.6ms。timeBeginPeriod(1) で 1ms へ
    # 引き上げることで SetTimer(16) がほぼ正確に 16ms 間隔で発火する。
    winmm = ctypes.windll.winmm
    winmm.timeBeginPeriod(1)

    # 累積デルタ
    accum = [0, 0]
    last_flush = time.perf_counter()

    def flush():
        nonlocal last_flush
        if accum[0] == 0 and accum[1] == 0:
            last_flush = time.perf_counter()
            return
        dx, dy = accum[0], accum[1]
        accum[0] = 0
        accum[1] = 0
        last_flush = time.perf_counter()
        on_delta(dx, dy)

    def wnd_proc(hwnd, msg_id, wparam, lparam):
        if msg_id == _WM_INPUT:
            buf = ctypes.create_string_buffer(256)
            size = wintypes.UINT(256)
            result = user32.GetRawInputData(
                lparam, _RID_INPUT, buf, byref(size),
                sizeof(_RAWINPUTHEADER),
            )
            if result > 0:
                raw = ctypes.cast(buf, POINTER(_RAWINPUT)).contents
                if (raw.header.dwType == _RIM_TYPEMOUSE
                        and not (raw.mouse.usFlags & _MOUSE_MOVE_ABSOLUTE)):
                    dx = raw.mouse.lLastX
                    dy = raw.mouse.lLastY
                    if dx != 0 or dy != 0:
                        accum[0] += dx
                        accum[1] += dy
            return 0
        if msg_id == _WM_TIMER and wparam == _FLUSH_TIMER_ID:
            flush()
            return 0
        return user32.DefWindowProcW(hwnd, msg_id, wparam, lparam)

    proc = _WNDPROC_TYPE(wnd_proc)
    hinstance = kernel32.GetModuleHandleW(None)

    wc = _WNDCLASSEXW()
    wc.cbSize = sizeof(_WNDCLASSEXW)
    wc.lpfnWndProc = proc
    wc.hInstance = hinstance
    wc.lpszClassName = "RawMouseInput"

    if not user32.RegisterClassExW(byref(wc)):
        print("[RawMouse] Failed to register window class")
        return

    hwnd = user32.CreateWindowExW(
        0, "RawMouseInput", "", 0,
        0, 0, 0, 0,
        None, None, hinstance, None,
    )
    if not hwnd:
        print("[RawMouse] Failed to create window")
        return

    rid = _RAWINPUTDEVICE()
    rid.usUsagePage = 0x01
    rid.usUsage = 0x02
    rid.dwFlags = _RIDEV_INPUTSINK
    rid.hwndTarget = hwnd
    if not user32.RegisterRawInputDevices(byref(rid), 1, sizeof(_RAWINPUTDEVICE)):
        print("[RawMouse] Failed to register raw input device")
        return

    # Timer で 16ms ごとに WM_TIMER を投げる。polling ベースより正確。
    if not user32.SetTimer(hwnd, _FLUSH_TIMER_ID, _FLUSH_INTERVAL_MS, None):
        print("[RawMouse] SetTimer failed; falling back to polling")

    print("[RawMouse] Raw mouse input listener started")

    # MsgWaitForMultipleObjectsEx でメッセージ到着 or タイムアウトで wake。
    # time.sleep による busy-wait を避け、Timer 精度にぶら下がる形にする。
    QS_ALLINPUT = 0x04FF
    MWMO_INPUTAVAILABLE = 0x0004
    msg = wintypes.MSG()
    PM_REMOVE = 0x0001
    try:
        while is_running():
            user32.MsgWaitForMultipleObjectsEx(
                0, None, _FLUSH_INTERVAL_MS, QS_ALLINPUT, MWMO_INPUTAVAILABLE,
            )
            while user32.PeekMessageW(byref(msg), hwnd, 0, 0, PM_REMOVE):
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))
    finally:
        for action, fn in (
            ("KillTimer", lambda: user32.KillTimer(hwnd, _FLUSH_TIMER_ID)),
            ("DestroyWindow", lambda: user32.DestroyWindow(hwnd)),
            ("timeEndPeriod", lambda: winmm.timeEndPeriod(1)),
        ):
            try:
                fn()
            except Exception:
                print(f"[RawMouse] {action} failed (ignored)")
