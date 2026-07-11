import ctypes
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SENDER_DIR = Path(__file__).resolve().parent.parent / "sender"
if str(_SENDER_DIR) not in sys.path:
    sys.path.insert(0, str(_SENDER_DIR))

import raw_mouse


class _FakeFn:
    """Stand-in for a ctypes DLL function proxy: accepts .argtypes/.restype
    assignment (ignored, like the real thing) and delegates calls either to
    a fixed return value or an injectable Python implementation."""

    def __init__(self, impl=None, ret=1):
        self.argtypes = None
        self.restype = None
        self._impl = impl
        self._ret = ret
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if self._impl is not None:
            return self._impl(*args)
        return self._ret


class _CapturingWndProcType:
    """Wraps raw_mouse._WNDPROC_TYPE so the test can grab the created
    WNDPROC callback without needing to read it back out of a byref'd
    ctypes Structure (CArgObject has no .contents outside a real FFI call).
    Still produces a real ctypes callback -- no OS hook is installed."""

    def __init__(self, real_type, sink):
        self._real_type = real_type
        self._sink = sink

    def __call__(self, fn):
        cb = self._real_type(fn)
        self._sink.append(cb)
        return cb


class FakeUser32:
    def __init__(self, wndproc_sink, register_class_ok=True, create_window_ok=True,
                 register_raw_input_ok=True, set_timer_ok=True, get_raw_input_impl=None):
        self._sink = wndproc_sink
        self._pending = []
        self._current = None

        self.RegisterClassExW = _FakeFn(ret=(1 if register_class_ok else 0))
        self.CreateWindowExW = _FakeFn(ret=(999 if create_window_ok else 0))
        self.RegisterRawInputDevices = _FakeFn(ret=(1 if register_raw_input_ok else 0))
        self.SetTimer = _FakeFn(ret=(1 if set_timer_ok else 0))
        self.GetRawInputData = _FakeFn(impl=get_raw_input_impl or (lambda *a: 0))
        self.DefWindowProcW = _FakeFn(ret=0)
        self.MsgWaitForMultipleObjectsEx = _FakeFn(ret=0)
        self.TranslateMessage = _FakeFn(ret=1)
        self.KillTimer = _FakeFn(ret=1)
        self.DestroyWindow = _FakeFn(ret=1)
        self.UnregisterClassW = _FakeFn(ret=1)

        def _peek(msg_ptr, hwnd, msg_min, msg_max, remove):
            if not self._pending:
                return 0
            self._current = self._pending.pop(0)
            return 1
        self.PeekMessageW = _FakeFn(impl=_peek)

        def _dispatch(msg_ptr):
            if self._current is not None and self._sink:
                msg_id, wparam, lparam = self._current
                self._sink[0](0, msg_id, wparam, lparam)
            self._current = None
            return 0
        self.DispatchMessageW = _FakeFn(impl=_dispatch)

    def queue(self, msg_id, wparam=0, lparam=0):
        self._pending.append((msg_id, wparam, lparam))


class FakeKernel32:
    def __init__(self):
        self.GetModuleHandleW = _FakeFn(ret=1)


class FakeWinmm:
    def __init__(self):
        self.timeBeginPeriod = _FakeFn(ret=0)
        self.timeEndPeriod = _FakeFn(ret=0)


def _make_get_raw_input_data(dx, dy):
    def impl(hrawinput, cmd, buf, size_ptr, header_size):
        raw = raw_mouse._RAWINPUT()
        raw.header.dwType = 0  # RIM_TYPEMOUSE
        raw.mouse.usFlags = 0  # relative movement
        raw.mouse.lLastX = dx
        raw.mouse.lLastY = dy
        ctypes.memmove(buf, ctypes.byref(raw), ctypes.sizeof(raw))
        return ctypes.sizeof(raw)
    return impl


class _RunNTimes:
    """is_running() stand-in: True for the first n calls, False after."""

    def __init__(self, n):
        self.n = n
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.calls <= self.n


def _run_raw_mouse(user32, wndproc_sink=None, kernel32=None, winmm=None, is_running=None, on_delta=None):
    kernel32 = kernel32 or FakeKernel32()
    winmm = winmm or FakeWinmm()
    is_running = is_running or (lambda: False)
    deltas = []
    on_delta_fn = on_delta or (lambda dx, dy: deltas.append((dx, dy)))
    capturing_type = _CapturingWndProcType(
        raw_mouse._WNDPROC_TYPE, wndproc_sink if wndproc_sink is not None else user32._sink
    )
    with patch.object(ctypes.windll, "user32", user32), \
         patch.object(ctypes.windll, "kernel32", kernel32), \
         patch.object(ctypes.windll, "winmm", winmm), \
         patch.object(raw_mouse, "_WNDPROC_TYPE", capturing_type):
        raw_mouse.run(is_running, on_delta_fn)
    return deltas


class SetTimerFallbackFlushTests(unittest.TestCase):
    """SetTimer failure must not silently drop accumulated deltas: every
    16ms poll wake must flush instead of waiting for a WM_TIMER that will
    never arrive."""

    def test_setTimer_failure_flushes_on_poll_wake(self):
        sink = []
        user32 = FakeUser32(
            sink, set_timer_ok=False,
            get_raw_input_impl=_make_get_raw_input_data(5, -3),
        )
        user32.queue(raw_mouse._WM_INPUT)
        deltas = _run_raw_mouse(user32, is_running=_RunNTimes(2))
        self.assertEqual(deltas, [(5, -3)])

    def test_setTimer_success_defers_flush_to_wm_timer(self):
        sink = []
        user32 = FakeUser32(
            sink, set_timer_ok=True,
            get_raw_input_impl=_make_get_raw_input_data(7, 2),
        )
        user32.queue(raw_mouse._WM_INPUT)
        # No WM_TIMER queued in this iteration: a poll wake alone must not
        # flush when the real timer is installed.
        deltas = _run_raw_mouse(user32, is_running=_RunNTimes(1))
        self.assertEqual(deltas, [])

    def test_setTimer_success_flushes_on_wm_timer_dispatch(self):
        sink = []
        user32 = FakeUser32(
            sink, set_timer_ok=True,
            get_raw_input_impl=_make_get_raw_input_data(7, 2),
        )
        user32.queue(raw_mouse._WM_INPUT)
        user32.queue(raw_mouse._WM_TIMER, wparam=raw_mouse._FLUSH_TIMER_ID)
        deltas = _run_raw_mouse(user32, is_running=_RunNTimes(1))
        self.assertEqual(deltas, [(7, 2)])


class ResourceCleanupTests(unittest.TestCase):
    """Every path after timeBeginPeriod(1) -- successful and each early
    return -- must run inside the same try/finally, calling only cleanup
    for resources actually acquired, and always ending the timer period."""

    def test_register_class_failure_only_ends_timer_period(self):
        sink = []
        user32 = FakeUser32(sink, register_class_ok=False)
        winmm = FakeWinmm()
        _run_raw_mouse(user32, winmm=winmm)
        self.assertEqual(len(user32.KillTimer.calls), 0)
        self.assertEqual(len(user32.DestroyWindow.calls), 0)
        self.assertEqual(len(user32.UnregisterClassW.calls), 0)
        self.assertEqual(len(winmm.timeEndPeriod.calls), 1)

    def test_create_window_failure_unregisters_class_and_ends_timer_period(self):
        sink = []
        user32 = FakeUser32(sink, create_window_ok=False)
        winmm = FakeWinmm()
        _run_raw_mouse(user32, winmm=winmm)
        self.assertEqual(len(user32.KillTimer.calls), 0)
        self.assertEqual(len(user32.DestroyWindow.calls), 0)
        self.assertEqual(len(user32.UnregisterClassW.calls), 1)
        self.assertEqual(len(winmm.timeEndPeriod.calls), 1)

    def test_register_raw_input_failure_destroys_window_and_class(self):
        sink = []
        user32 = FakeUser32(sink, register_raw_input_ok=False)
        winmm = FakeWinmm()
        _run_raw_mouse(user32, winmm=winmm)
        self.assertEqual(len(user32.KillTimer.calls), 0)
        self.assertEqual(len(user32.DestroyWindow.calls), 1)
        self.assertEqual(len(user32.UnregisterClassW.calls), 1)
        self.assertEqual(len(winmm.timeEndPeriod.calls), 1)

    def test_full_success_releases_every_acquired_resource(self):
        sink = []
        user32 = FakeUser32(sink)
        winmm = FakeWinmm()
        _run_raw_mouse(user32, winmm=winmm, is_running=_RunNTimes(0))
        self.assertEqual(len(user32.KillTimer.calls), 1)
        self.assertEqual(len(user32.DestroyWindow.calls), 1)
        self.assertEqual(len(user32.UnregisterClassW.calls), 1)
        self.assertEqual(len(winmm.timeEndPeriod.calls), 1)

    def test_get_module_handle_failure_still_ends_timer_period(self):
        """GetModuleHandleW happens after timeBeginPeriod(1); if it raises,
        no resource acquired after it (class/window/timer) exists, so no
        cleanup for those should run, but timeEndPeriod(1) still must."""
        sink = []
        user32 = FakeUser32(sink)
        kernel32 = FakeKernel32()
        kernel32.GetModuleHandleW = _FakeFn(
            impl=lambda *a: (_ for _ in ()).throw(OSError("boom"))
        )
        winmm = FakeWinmm()
        with self.assertRaises(OSError):
            _run_raw_mouse(user32, kernel32=kernel32, winmm=winmm)
        self.assertEqual(len(user32.RegisterClassExW.calls), 0)
        self.assertEqual(len(user32.CreateWindowExW.calls), 0)
        self.assertEqual(len(user32.KillTimer.calls), 0)
        self.assertEqual(len(user32.DestroyWindow.calls), 0)
        self.assertEqual(len(user32.UnregisterClassW.calls), 0)
        self.assertEqual(len(winmm.timeEndPeriod.calls), 1)

    def test_cleanup_failure_is_best_effort_and_does_not_raise(self):
        sink = []
        user32 = FakeUser32(sink)
        user32.KillTimer = _FakeFn(impl=lambda *a: (_ for _ in ()).throw(OSError("boom")))
        winmm = FakeWinmm()
        # Must not raise despite KillTimer failing; remaining cleanup steps
        # still run.
        _run_raw_mouse(user32, winmm=winmm, is_running=_RunNTimes(0))
        self.assertEqual(len(user32.DestroyWindow.calls), 1)
        self.assertEqual(len(user32.UnregisterClassW.calls), 1)
        self.assertEqual(len(winmm.timeEndPeriod.calls), 1)


if __name__ == "__main__":
    unittest.main()
