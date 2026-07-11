import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

_RECEIVER_DIR = Path(__file__).resolve().parent.parent / "receiver"
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))

import input_injector
import input_server


class FakeSendInput:
    """Stand-in for input_injector._send_input: records the INPUT struct
    fields that would have been sent to the OS instead of actually calling
    user32.SendInput, and lets a test force the next call to "fail"."""

    def __init__(self):
        self.calls = []
        self.next_fail = False

    def __call__(self, inp):
        if inp.type == input_injector.INPUT_KEYBOARD:
            record = {"kind": "key", "vk": inp.union.ki.wVk, "flags": inp.union.ki.dwFlags}
        else:
            record = {"kind": "mouse", "flags": inp.union.mi.dwFlags, "data": inp.union.mi.mouseData}
        self.calls.append(record)
        if self.next_fail:
            self.next_fail = False
            return False
        return True

    def key_up_vks(self):
        return {
            c["vk"] for c in self.calls
            if c["kind"] == "key" and c["flags"] & input_injector.KEYEVENTF_KEYUP
        }


class InjectorIdentityTests(unittest.TestCase):
    """Exercise the real input_injector module with only the OS syscall
    boundary (_send_input) replaced by a fake, so no real keys/buttons are
    ever injected on the test machine."""

    def setUp(self):
        self.fake = FakeSendInput()
        self._patcher = patch.object(input_injector, "_send_input", self.fake)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_right_modifiers_and_win_keys_release_by_exact_vk(self):
        # Right Shift/Ctrl/Alt (161/163/165) and both Win keys (91/92).
        vks = (161, 163, 165, 91, 92)
        identities = []
        for vk in vks:
            identity = input_injector.replay_event(
                {"type": "key_down", "key": "shift", "vk": vk}
            )
            self.assertEqual(identity, ("vk", vk))
            identities.append(identity)

        input_injector.release_identities(identities)

        self.assertEqual(self.fake.key_up_vks(), set(vks))

    def test_simultaneous_left_right_modifiers_are_independent(self):
        left = input_injector.replay_event({"type": "key_down", "key": "shift", "vk": 160})
        right = input_injector.replay_event({"type": "key_down", "key": "shift", "vk": 161})
        self.assertEqual(left, ("vk", 160))
        self.assertEqual(right, ("vk", 161))
        self.assertNotEqual(left, right)

        # Releasing only one must not release the other.
        input_injector.release_identities([left])
        self.assertEqual(self.fake.key_up_vks(), {160})

        input_injector.release_identities([right])
        self.assertEqual(self.fake.key_up_vks(), {160, 161})

    def test_mouse_button_uses_exact_release_path(self):
        identity = input_injector.replay_event({"type": "key_down", "key": "mouse_right"})
        self.assertEqual(identity, ("mouse", "mouse_right"))

        input_injector.release_identities([identity])

        mouse_ups = [
            c for c in self.fake.calls
            if c["kind"] == "mouse" and c["flags"] == input_injector.MOUSEEVENTF_RIGHTUP
        ]
        self.assertEqual(len(mouse_ups), 1)

    def test_failed_key_injection_is_not_tracked(self):
        self.fake.next_fail = True
        identity = input_injector.replay_event({"type": "key_down", "key": "a", "vk": 65})
        self.assertIsNone(identity)

    def test_unsupported_mouse_button_is_not_tracked(self):
        identity = input_injector.replay_event({"type": "key_down", "key": "mouse_unknown"})
        self.assertIsNone(identity)
        self.assertEqual(self.fake.calls, [])

    def test_mouse_move_and_scroll_never_produce_identity(self):
        self.assertIsNone(input_injector.replay_event({"type": "mouse_move", "dx": 5, "dy": -3}))
        self.assertIsNone(input_injector.replay_event({"type": "mouse_scroll", "dx": 0, "dy": 1}))


class FakeInjector:
    """Fake input_injector for receiver-side lock/lifecycle tests: mirrors
    the real replay_event/release_identities contract without touching
    ctypes or the OS at all."""

    def __init__(self):
        self.injected = []   # (kind, value, is_down) for every successful injection
        self.released = []   # flattened identities passed to release_identities
        self.fail_next = False
        self.on_replay = None  # optional hook called from inside replay_event

    def replay_event(self, event):
        if self.on_replay:
            self.on_replay(event)
        etype = event.get("type")
        if etype not in ("key_down", "key_up"):
            return None
        is_down = etype == "key_down"
        if self.fail_next:
            self.fail_next = False
            return None
        key = event.get("key", "")
        if key.startswith("mouse_"):
            self.injected.append(("mouse", key, is_down))
            return ("mouse", key)
        vk = event.get("vk")
        if vk is None:
            return None
        self.injected.append(("vk", vk, is_down))
        return ("vk", vk)

    def release_identities(self, identities):
        self.released.extend(identities)


class RemoteControlLifecycleTests(unittest.TestCase):
    """Exercise input_server's RC lock/lifecycle (_rc_inject_event,
    _set_rc_state) with a fake injector; never imports/uses the real
    ctypes-based input_injector here."""

    def setUp(self):
        self.fake = FakeInjector()
        self._orig_injector = input_server.input_injector
        input_server.input_injector = self.fake
        input_server.remote_control_enabled = False
        input_server._rc_active_identities.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        input_server.input_injector = self._orig_injector
        input_server.remote_control_enabled = False
        input_server._rc_active_identities.clear()

    def test_keydown_keyup_round_trip_tracks_and_clears(self):
        input_server.remote_control_enabled = True
        input_server._rc_inject_event({"type": "key_down", "key": "a", "vk": 65})
        self.assertEqual(input_server._rc_active_identities, {("vk", 65)})

        input_server._rc_inject_event({"type": "key_up", "key": "a", "vk": 65})
        self.assertEqual(input_server._rc_active_identities, set())

    def test_repeated_up_down_events_are_harmless(self):
        input_server.remote_control_enabled = True
        down = {"type": "key_down", "key": "a", "vk": 65}
        up = {"type": "key_up", "key": "a", "vk": 65}

        input_server._rc_inject_event(down)
        input_server._rc_inject_event(down)  # repeated down: still one entry
        self.assertEqual(input_server._rc_active_identities, {("vk", 65)})

        input_server._rc_inject_event(up)
        input_server._rc_inject_event(up)  # repeated up: no-op, no error
        self.assertEqual(input_server._rc_active_identities, set())

    def test_disabled_state_ignores_events_without_injecting(self):
        input_server.remote_control_enabled = False
        input_server._rc_inject_event({"type": "key_down", "key": "a", "vk": 65})
        self.assertEqual(self.fake.injected, [])
        self.assertEqual(input_server._rc_active_identities, set())

    def test_failed_injection_is_not_tracked(self):
        input_server.remote_control_enabled = True
        self.fake.fail_next = True
        input_server._rc_inject_event({"type": "key_down", "key": "a", "vk": 65})
        self.assertEqual(input_server._rc_active_identities, set())

    def test_disable_releases_exact_snapshot_by_identity(self):
        input_server.remote_control_enabled = True
        input_server._rc_inject_event({"type": "key_down", "key": "shift", "vk": 161})
        input_server._rc_inject_event({"type": "key_down", "key": "shift", "vk": 160})
        input_server._rc_inject_event({"type": "key_down", "key": "mouse_right"})

        input_server._set_rc_state(False)

        self.assertEqual(
            set(self.fake.released), {("vk", 161), ("vk", 160), ("mouse", "mouse_right")}
        )
        self.assertEqual(input_server._rc_active_identities, set())
        self.assertFalse(input_server.remote_control_enabled)

    def test_racing_keydown_and_disable_cannot_leak_or_double_inject(self):
        """A key-down whose replay is in-flight while a concurrent disable
        is requested must either be fully tracked-and-released, or fully
        rejected -- never left dangling and never injected again after OFF.

        Determinism comes from _rc_lock itself (not from sleeps): the
        on_replay hook runs while _rc_inject_event still holds _rc_lock, so
        _rc_lock.locked() is guaranteed True at that point, and the disable
        thread is guaranteed to block on the same lock until the hook lets
        the injection finish.
        """
        order = []
        order_lock = threading.Lock()
        started = threading.Event()
        proceed = threading.Event()

        def on_replay(event):
            with order_lock:
                order.append("inject_start")
            started.set()
            proceed.wait(timeout=5)
            with order_lock:
                order.append("inject_end")

        self.fake.on_replay = on_replay
        input_server.remote_control_enabled = True
        down_event = {"type": "key_down", "key": "a", "vk": 65}

        t_inject = threading.Thread(target=input_server._rc_inject_event, args=(down_event,))
        t_inject.start()
        self.assertTrue(started.wait(timeout=5), "injection did not start")

        # _rc_inject_event must still be holding _rc_lock here: the on_replay
        # hook only returns once `proceed` is set, and that call happens
        # inside the `with _rc_lock:` block.
        self.assertTrue(input_server._rc_lock.locked())

        def do_disable():
            with order_lock:
                order.append("disable_start")
            input_server._set_rc_state(False)
            with order_lock:
                order.append("disable_end")

        t_disable = threading.Thread(target=do_disable)
        t_disable.start()

        proceed.set()
        t_inject.join(timeout=5)
        t_disable.join(timeout=5)

        # The in-flight down must have been fully applied (tracked) before
        # disable's snapshot+clear could run, so it ends up released, not
        # leaked, and no further injection happens after disable.
        self.assertEqual(self.fake.injected, [("vk", 65, True)])
        self.assertEqual(self.fake.released, [("vk", 65)])
        self.assertEqual(input_server._rc_active_identities, set())
        self.assertFalse(input_server.remote_control_enabled)

    def test_keydown_after_disable_never_injects(self):
        input_server.remote_control_enabled = True
        input_server._rc_inject_event({"type": "key_down", "key": "a", "vk": 65})
        input_server._set_rc_state(False)
        self.fake.injected.clear()
        self.fake.released.clear()

        # A late-arriving down for an already-disabled RC must be ignored.
        input_server._rc_inject_event({"type": "key_down", "key": "b", "vk": 66})

        self.assertEqual(self.fake.injected, [])
        self.assertEqual(input_server._rc_active_identities, set())


if __name__ == "__main__":
    unittest.main()
