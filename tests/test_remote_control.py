import asyncio
import contextlib
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

_RECEIVER_DIR = Path(__file__).resolve().parent.parent / "receiver"
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))

_SENDER_DIR = Path(__file__).resolve().parent.parent / "sender"
if str(_SENDER_DIR) not in sys.path:
    sys.path.insert(0, str(_SENDER_DIR))

import input_injector
import input_sender
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
        # These tests exercise the inject/lock lifecycle itself, not the
        # sender-readiness gate, so start as already-synchronized.
        input_server._sender_synchronized = True
        self.addCleanup(self._restore)

    def _restore(self):
        input_server.input_injector = self._orig_injector
        input_server.remote_control_enabled = False
        input_server._rc_active_identities.clear()
        input_server._sender_synchronized = False

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


class FakeSenderConn:
    """Stand-in for the receiver-side sender websocket connection: async
    iterable over a fixed list of already-JSON-encoded messages, plus an
    async send() that just records what was sent. No real socket."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.remote_address = ("127.0.0.1", 0)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send(self, msg):
        self.sent.append(msg)


class SenderHandlerReadinessTests(unittest.TestCase):
    """receiver's sender_handler / _sender_ready fail-closed gating, driven
    through fake connections (no real websocket) with a fake injector."""

    def setUp(self):
        self.fake = FakeInjector()
        self._orig_injector = input_server.input_injector
        input_server.input_injector = self.fake
        input_server.remote_control_enabled = False
        input_server._rc_active_identities.clear()
        input_server.sender_ws = None
        input_server._sender_synchronized = False
        self.addCleanup(self._restore)

    def _restore(self):
        input_server.input_injector = self._orig_injector
        input_server.remote_control_enabled = False
        input_server._rc_active_identities.clear()
        input_server.sender_ws = None
        input_server._sender_synchronized = False

    def test_event_before_state_message_is_not_injected(self):
        # Even if a stale ON state were somehow left over, a fresh
        # connection must stay fail-closed until it reports in itself.
        input_server.remote_control_enabled = True
        conn = FakeSenderConn([
            json.dumps({"type": "key_down", "key": "a", "vk": 65}),
        ])
        asyncio.run(input_server.sender_handler(conn))
        self.assertEqual(self.fake.injected, [])

    def test_initial_explicit_false_keeps_injection_off(self):
        conn = FakeSenderConn([
            json.dumps({"type": "remote_control", "enabled": False}),
            json.dumps({"type": "key_down", "key": "a", "vk": 65}),
        ])
        asyncio.run(input_server.sender_handler(conn))
        self.assertEqual(self.fake.injected, [])
        self.assertFalse(input_server.remote_control_enabled)

    def test_initial_true_enables_and_injects_after_ack(self):
        conn = FakeSenderConn([
            json.dumps({"type": "remote_control", "enabled": True}),
            json.dumps({"type": "key_down", "key": "a", "vk": 65}),
        ])
        asyncio.run(input_server.sender_handler(conn))
        self.assertEqual(self.fake.injected, [("vk", 65, True)])

    def test_disconnect_resets_readiness_and_disables_active_state(self):
        conn = FakeSenderConn([
            json.dumps({"type": "remote_control", "enabled": True}),
        ])
        asyncio.run(input_server.sender_handler(conn))
        self.assertFalse(input_server.remote_control_enabled)
        self.assertFalse(input_server._sender_synchronized)
        self.assertIsNone(input_server.sender_ws)

        # Reconnect: injection must not resume before the new connection
        # synchronizes again, even though it is the "same" sender.
        conn2 = FakeSenderConn([
            json.dumps({"type": "key_down", "key": "b", "vk": 66}),
        ])
        asyncio.run(input_server.sender_handler(conn2))
        self.assertEqual(self.fake.injected, [])


class RemoteControlApiGatingTests(unittest.TestCase):
    """_api_post_remote_control fail-closed / pending-enable behavior, with
    _send_command_to_sender and _notify_sender_async replaced by fakes so no
    asyncio loop or real websocket is needed."""

    def setUp(self):
        input_server.remote_control_enabled = False
        input_server._rc_active_identities.clear()
        input_server.sender_ws = None
        input_server._sender_synchronized = False
        self._orig_send_command = input_server._send_command_to_sender
        self._orig_notify_async = input_server._notify_sender_async
        self.sent_commands = []
        self.notified = []

        def fake_send_command(data):
            self.sent_commands.append(data)
            return True

        def fake_notify(data):
            self.notified.append(data)

        input_server._send_command_to_sender = fake_send_command
        input_server._notify_sender_async = fake_notify
        self.addCleanup(self._restore)

    def _restore(self):
        input_server.remote_control_enabled = False
        input_server._rc_active_identities.clear()
        input_server.sender_ws = None
        input_server._sender_synchronized = False
        input_server._send_command_to_sender = self._orig_send_command
        input_server._notify_sender_async = self._orig_notify_async

    def test_enable_rejected_when_no_sender(self):
        body = json.dumps({"enabled": True}).encode()
        with self.assertRaises(input_server.ApiError) as ctx:
            input_server._api_post_remote_control(None, body)
        self.assertEqual(ctx.exception.status, 409)
        self.assertFalse(input_server.remote_control_enabled)
        self.assertEqual(self.sent_commands, [])

    def test_enable_rejected_when_connected_but_unsynchronized(self):
        input_server.sender_ws = object()
        input_server._sender_synchronized = False
        body = json.dumps({"enabled": True}).encode()
        with self.assertRaises(input_server.ApiError) as ctx:
            input_server._api_post_remote_control(None, body)
        self.assertEqual(ctx.exception.status, 409)
        self.assertFalse(input_server.remote_control_enabled)
        self.assertEqual(self.sent_commands, [])

    def test_enable_with_synchronized_sender_sends_but_waits_for_ack(self):
        input_server.sender_ws = object()
        input_server._sender_synchronized = True
        body = json.dumps({"enabled": True}).encode()

        result = input_server._api_post_remote_control(None, body)

        self.assertEqual(result, {"ok": True, "enabled": True})
        self.assertEqual(self.sent_commands, [{"type": "remote_control", "enabled": True}])
        # Must not be enabled locally until the sender itself acknowledges.
        self.assertFalse(input_server.remote_control_enabled)

        # Simulate the sender's acknowledgement (as sender_handler would
        # apply it): only now does local state/injection become enabled.
        input_server._set_rc_state(True, mark_synchronized=True)
        self.assertTrue(input_server.remote_control_enabled)

    def test_enable_send_failure_is_rejected_without_changing_state(self):
        input_server.sender_ws = object()
        input_server._sender_synchronized = True
        input_server._send_command_to_sender = lambda data: (
            self.sent_commands.append(data), False,
        )[1]
        body = json.dumps({"enabled": True}).encode()

        with self.assertRaises(input_server.ApiError) as ctx:
            input_server._api_post_remote_control(None, body)

        self.assertEqual(ctx.exception.status, 502)
        self.assertFalse(input_server.remote_control_enabled)

    def test_disable_is_immediate_even_without_sender(self):
        input_server.remote_control_enabled = True
        body = json.dumps({"enabled": False}).encode()

        result = input_server._api_post_remote_control(None, body)

        self.assertEqual(result, {"ok": True, "enabled": False})
        self.assertFalse(input_server.remote_control_enabled)
        self.assertEqual(self.notified, [{"type": "remote_control", "enabled": False}])


class FakeSenderTransport:
    """Stand-in for the sender's outgoing websocket connection object used
    inside input_sender.sender(): records every sent message and never
    yields incoming messages. No real socket is opened."""

    def __init__(self):
        self.sent = []
        self.first_send = asyncio.Event()

    async def send(self, msg):
        self.sent.append(msg)
        self.first_send.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()  # never yields; connection stays "open"


class FakeConnectContext:
    """Stand-in for `websockets.connect(uri)`: an async context manager
    that always yields the same fake transport."""

    def __init__(self, ws):
        self._ws = ws

    def __call__(self, uri):
        return self

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, exc_type, exc, tb):
        return False


class SenderAnnouncesExplicitStateOnConnectTests(unittest.IsolatedAsyncioTestCase):
    """input_sender.sender() must send its own explicit remote_control state
    (True or False) immediately after connecting, before normal queued input
    handling starts -- never the old "send only when ON" behavior."""

    def tearDown(self):
        input_sender.remote.mode = False
        input_sender.ws_status = "disconnected"
        input_sender.ws_connection = None

    async def _run_and_capture_first_send(self, initial_mode):
        input_sender.remote.mode = initial_mode
        fake_ws = FakeSenderTransport()

        # Replace the two long-lived loop tasks with harmless stubs: this
        # test only cares about what sender() sends immediately after
        # connecting, not about the (module-global, cross-test-shared)
        # event queue or receiver-message handling.
        async def _noop_forever(*_args, **_kwargs):
            await asyncio.Event().wait()

        with patch.object(input_sender.websockets, "connect", FakeConnectContext(fake_ws)), \
             patch.object(input_sender, "_send_loop", _noop_forever), \
             patch.object(input_sender, "_recv_from_receiver", _noop_forever):
            task = asyncio.ensure_future(input_sender.sender("dummyhost", 1))
            try:
                await asyncio.wait_for(fake_ws.first_send.wait(), timeout=5)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
        return fake_ws.sent

    async def test_sends_explicit_false_on_connect(self):
        sent = await self._run_and_capture_first_send(False)
        self.assertEqual(sent, [json.dumps({"type": "remote_control", "enabled": False})])

    async def test_sends_explicit_true_on_connect(self):
        sent = await self._run_and_capture_first_send(True)
        self.assertEqual(sent, [json.dumps({"type": "remote_control", "enabled": True})])


if __name__ == "__main__":
    unittest.main()
