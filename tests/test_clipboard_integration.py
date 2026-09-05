import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "sender"))
sys.path.insert(0, str(_ROOT / "receiver"))

_TEST_CONFIG = tempfile.TemporaryDirectory()
_OLD_CONFIG_DIR = os.environ.get("INPUT_RELAY_CONFIG_DIR")
os.environ["INPUT_RELAY_CONFIG_DIR"] = _TEST_CONFIG.name
try:
    import clipboard_client
    import clipboard_server
    import http_api
    import input_server
    import input_sender
    import notification_window
finally:
    if _OLD_CONFIG_DIR is None:
        os.environ.pop("INPUT_RELAY_CONFIG_DIR", None)
    else:
        os.environ["INPUT_RELAY_CONFIG_DIR"] = _OLD_CONFIG_DIR


class FakeNotification:
    def __init__(self):
        self.available = True
        self.messages = []

    def show(self, text):
        self.messages.append(text)
        return True

    def shutdown(self):
        pass


class FakeWorker:
    def __init__(self, on_local=None, on_error=None):
        self.on_local = on_local
        self.on_error = on_error
        self.enabled = None
        self.writes = []
        self.disabled = 0
        self.stopped = 0

    def start(self):
        return True

    def enable(self, epoch):
        self.enabled = epoch

    def disable(self):
        self.enabled = None
        self.disabled += 1

    def stop(self):
        self.stopped += 1

    def submit_write(self, epoch, revision, text, *, skip_sequence=None):
        self.writes.append((epoch, revision, text, skip_sequence))
        return True


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))


class FakeInputWS(FakeWS):
    def __init__(self, messages=(), path="/"):
        super().__init__()
        self.messages = list(messages)
        self.remote_address = ("127.0.0.1", 1)
        self.request = type("Request", (), {"path": path})()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def close(self):
        self.closed = True

    async def recv(self):
        if not self.messages:
            raise EOFError()
        return self.messages.pop(0)


class FakeClipboardHub:
    def __init__(self):
        self.handled = []
        self.unregistered = []

    async def register_sender(self, ws):
        return {
            "type": "clipboard_offer",
            "version": 1,
            "session": "00000000-0000-4000-8000-000000000001",
        }

    async def unregister_sender(self, ws, session):
        self.unregistered.append((ws, session))

    async def handle(self, ws):
        self.handled.append(ws)


class ClipboardClientStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.notice = FakeNotification()
        self.client = clipboard_client.ClipboardClient(
            asyncio.get_running_loop(), self.notice, worker_factory=FakeWorker,
        )
        self.client._worker = FakeWorker()
        self.client._set_state("off", "user")
        self.ws = FakeWS()

    async def test_off_on_off_state_machine_uses_explicit_requests(self):
        deadline = await self.client._handle_toggle(self.ws)
        self.assertIsNotNone(deadline)
        self.assertEqual(self.client.snapshot()["state"], "enabling")
        request_id = self.ws.sent[-1]["request_id"]
        epoch = "00000000-0000-4000-8000-000000000001"
        await self.client._handle_message(self.ws, {
            "type": "clipboard_state",
            "request_id": request_id,
            "enabled": True,
            "epoch": epoch,
            "reason": "user",
        }, deadline)
        self.assertTrue(self.client.snapshot()["enabled"])

        await self.client._handle_toggle(self.ws)
        self.assertEqual(self.client.snapshot()["state"], "disabling")
        self.assertIsNone(self.client._worker.enabled)
        self.assertFalse(self.ws.sent[-1]["enabled"])

    async def test_stale_epoch_update_cannot_write(self):
        self.client._epoch = "00000000-0000-4000-8000-000000000002"
        self.client._set_state("on", "user")
        await self.client._handle_message(self.ws, {
            "type": "clipboard_update",
            "epoch": "00000000-0000-4000-8000-000000000001",
            "revision": 99,
            "origin": "sub",
            "origin_seq": 1,
            "text": "stale",
        }, None)
        self.assertEqual(self.client._worker.writes, [])

    async def test_own_confirmed_update_passes_sequence_for_no_rewrite(self):
        epoch = "00000000-0000-4000-8000-000000000001"
        self.client._epoch = epoch
        self.client._last_local = (7, 1234)
        self.client._set_state("on", "user")
        await self.client._handle_message(self.ws, {
            "type": "clipboard_update",
            "epoch": epoch,
            "revision": 1,
            "origin": "main",
            "origin_seq": 7,
            "text": "same",
        }, None)
        self.assertEqual(self.client._worker.writes[-1][-1], 1234)

    async def test_distinct_rapid_hotkey_presses_are_kept_in_bounded_control_queue(self):
        self.client._enqueue_toggle()
        self.client._enqueue_toggle()
        self.assertEqual(self.client._commands.qsize(), 2)
        for _ in range(10):
            self.client._enqueue_toggle()
        self.assertLessEqual(self.client._commands.qsize(), 4)


class ClipboardReceiverIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_misplaced_clipboard_payload_never_reaches_browser_or_injector(self):
        hub = FakeClipboardHub()
        conn = FakeInputWS([json.dumps({
            "type": "clipboard_propose",
            "epoch": "00000000-0000-4000-8000-000000000001",
            "origin_seq": 1,
            "text": "body-marker-never-forward",
        })])
        broadcasts = []
        with patch.object(input_server, "_clipboard_hub", hub), \
             patch.object(input_server, "_rc_inject_event") as inject, \
             patch.object(
                 input_server,
                 "broadcast_to_browsers",
                 lambda raw: self._record_async(broadcasts, raw),
             ):
            await input_server.sender_handler(conn)
        inject.assert_not_called()
        self.assertNotIn("body-marker-never-forward", "".join(broadcasts))
        self.assertEqual([json.loads(raw)["type"] for raw in broadcasts], ["input_reset"])
        self.assertEqual(conn.sent[1]["type"], "clipboard_offer")
        self.assertEqual(len(hub.unregistered), 1)

    async def test_clipboard_path_is_explicit_and_standalone_rejects_it(self):
        hub = FakeClipboardHub()
        conn = FakeInputWS(path="/clipboard")
        with patch.object(input_server, "_clipboard_hub", hub):
            await input_server.ws_handler(conn)
        self.assertEqual(hub.handled, [conn])

        standalone_conn = FakeInputWS(path="/clipboard")
        with patch.object(input_server, "_clipboard_hub", None):
            await input_server.ws_handler(standalone_conn)
        self.assertTrue(standalone_conn.closed)

    async def test_stale_session_bind_is_closed_without_starting_worker(self):
        starts = []

        def worker_factory(*_args):
            starts.append(True)
            return FakeWorker()

        hub = clipboard_server.ClipboardServerHub(
            asyncio.get_running_loop(), worker_factory=worker_factory,
        )
        await hub.register_sender(object())
        conn = FakeInputWS([json.dumps({
            "type": "clipboard_bind",
            "version": 1,
            "session": "00000000-0000-4000-8000-000000000099",
        })], path="/clipboard")
        await hub.handle(conn)
        self.assertTrue(conn.closed)
        self.assertEqual(starts, [])

    async def test_old_sender_cleanup_cannot_invalidate_new_session(self):
        hub = clipboard_server.ClipboardServerHub(asyncio.get_running_loop())
        old_ws = object()
        old = await hub.register_sender(old_ws)
        new_ws = object()
        new = await hub.register_sender(new_ws)
        await hub.unregister_sender(old_ws, old["session"])
        self.assertIs(hub._sender_ws, new_ws)
        self.assertEqual(hub._session, new["session"])

    async def test_delayed_old_register_cleanup_cannot_stop_new_session_worker(self):
        workers = []

        def worker_factory(*args):
            worker = FakeWorker(*args)
            workers.append(worker)
            return worker

        class BoundWS(FakeInputWS):
            def __init__(inner_self, bind, *, delay_close=False):
                super().__init__([json.dumps(bind)], path="/clipboard")
                inner_self.initial_state = asyncio.Event()
                inner_self.close_started = asyncio.Event()
                inner_self.close_release = asyncio.Event()
                inner_self.finished = asyncio.Event()
                if not delay_close:
                    inner_self.close_release.set()

            async def send(inner_self, raw):
                await super(BoundWS, inner_self).send(raw)
                inner_self.initial_state.set()

            async def recv(inner_self):
                if inner_self.messages:
                    return inner_self.messages.pop(0)
                await inner_self.finished.wait()
                raise EOFError()

            async def close(inner_self):
                inner_self.close_started.set()
                await inner_self.close_release.wait()
                inner_self.closed = True
                inner_self.finished.set()

        hub = clipboard_server.ClipboardServerHub(
            asyncio.get_running_loop(), worker_factory=worker_factory,
        )
        first_offer = await hub.register_sender(object())
        old_conn = BoundWS({
            "type": "clipboard_bind", "version": 1,
            "session": first_offer["session"],
        }, delay_close=True)
        old_handler = asyncio.create_task(hub.handle(old_conn))
        await asyncio.wait_for(old_conn.initial_state.wait(), 1)
        self.assertEqual(len(workers), 1)

        middle_sender = object()
        delayed_register = asyncio.create_task(hub.register_sender(middle_sender))
        await asyncio.wait_for(old_conn.close_started.wait(), 1)

        newest_sender = object()
        newest_offer = await hub.register_sender(newest_sender)
        new_conn = BoundWS({
            "type": "clipboard_bind", "version": 1,
            "session": newest_offer["session"],
        })
        new_handler = asyncio.create_task(hub.handle(new_conn))
        await asyncio.wait_for(new_conn.initial_state.wait(), 1)
        self.assertEqual(len(workers), 2)
        self.assertIs(hub._worker, workers[1])

        old_conn.close_release.set()
        middle_offer = await asyncio.wait_for(delayed_register, 1)
        await asyncio.wait_for(old_handler, 1)
        self.assertEqual(workers[0].stopped, 1)
        self.assertEqual(workers[1].stopped, 0)
        self.assertIs(hub._worker, workers[1])
        self.assertIs(hub._sender_ws, newest_sender)

        # The middle input handler can unwind after the newest bind.  Its
        # unregister must observe the same owner identity and leave W2 alone.
        await hub.unregister_sender(middle_sender, middle_offer["session"])
        self.assertEqual(workers[1].stopped, 0)
        self.assertIs(hub._worker, workers[1])

        new_conn.finished.set()
        await asyncio.wait_for(new_handler, 1)
        self.assertEqual(workers[1].stopped, 1)

    async def test_bound_connection_orders_main_proposal_and_returns_to_off(self):
        workers = []

        def worker_factory(*args):
            worker = FakeWorker(*args)
            workers.append(worker)
            return worker

        hub = clipboard_server.ClipboardServerHub(
            asyncio.get_running_loop(), worker_factory=worker_factory,
        )
        sender = object()
        offer = await hub.register_sender(sender)
        epoch_placeholder = None
        followup_stage = 0

        # The epoch is generated while processing clipboard_set, so feed the
        # proposal from a recv() implementation after observing the ON state.
        class StatefulWS(FakeInputWS):
            async def recv(inner_self):
                nonlocal epoch_placeholder, followup_stage
                if inner_self.messages:
                    return inner_self.messages.pop(0)
                if epoch_placeholder is None:
                    while not inner_self.sent or not inner_self.sent[-1].get("enabled"):
                        await asyncio.sleep(0)
                    epoch_placeholder = inner_self.sent[-1]["epoch"]
                    return json.dumps({
                        "type": "clipboard_propose",
                        "epoch": epoch_placeholder,
                        "origin_seq": 1,
                        "text": "ordered-main",
                    })
                if followup_stage == 0:
                    followup_stage = 1
                    return json.dumps({
                        "type": "clipboard_set", "request_id": 2, "enabled": False,
                    })
                raise EOFError()

        conn = StatefulWS([
            json.dumps({
                "type": "clipboard_bind", "version": 1, "session": offer["session"],
            }),
            json.dumps({
                "type": "clipboard_set", "request_id": 1, "enabled": True,
            }),
        ], path="/clipboard")
        await hub.handle(conn)
        types = [message["type"] for message in conn.sent]
        self.assertEqual(types[:3], [
            "clipboard_state", "clipboard_state", "clipboard_update",
        ])
        self.assertEqual(conn.sent[2]["revision"], 1)
        self.assertEqual(conn.sent[2]["origin"], "main")
        self.assertEqual(workers[0].writes[0][2], "ordered-main")

    @staticmethod
    async def _record_async(items, value):
        items.append(value)


class SenderStatusTests(unittest.TestCase):
    def test_status_contains_only_clipboard_state_not_text_or_epoch(self):
        ctx = http_api.SenderContext(
            gui_path=Path(__file__),
            get_config=lambda: {"host": "sub", "port": 8888},
            save_config=lambda _data: None,
            trigger_reconnect=lambda: None,
            get_gamepad=lambda: None,
            valid_overlay_positions=(),
            get_ws_status=lambda: "connected",
            get_remote_mode=lambda: False,
            get_clipboard_status=lambda: {
                "state": "on", "enabled": True, "reason": "user",
            },
            get_input_timestamps=lambda: (0.0, 0.0),
        )
        handler_cls = http_api.make_handler(ctx)
        handler = object.__new__(handler_cls)
        handler.path = "/api/status"
        responses = []
        handler._send_json = lambda data, status=200: responses.append(data)
        handler.do_GET()
        clipboard = responses[0]["clipboard"]
        self.assertEqual(clipboard, {
            "state": "on", "enabled": True, "reason": "user",
        })
        self.assertNotIn("text", clipboard)
        self.assertNotIn("epoch", clipboard)


class ClipboardHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.down = set()
        self.hotkey = input_sender.ClipboardHotkeyState(lambda vk: vk in self.down)

    def test_shift_scroll_selects_clipboard_and_repeat_is_consumed(self):
        self.down.add(0xA0)
        self.hotkey.press(input_sender.keyboard.Key.shift_l, 0xA0)
        self.assertEqual(
            self.hotkey.press(input_sender.keyboard.Key.scroll_lock, 0x91),
            "clipboard",
        )
        self.assertEqual(
            self.hotkey.press(input_sender.keyboard.Key.scroll_lock, 0x91),
            "consume",
        )
        self.assertTrue(self.hotkey.release(input_sender.keyboard.Key.scroll_lock, 0x91))

    def test_both_shifts_remain_active_until_both_are_released(self):
        self.down.update({0xA0, 0xA1})
        self.hotkey.press(input_sender.keyboard.Key.shift_l, 0xA0)
        self.hotkey.press(input_sender.keyboard.Key.shift_r, 0xA1)
        self.down.remove(0xA0)
        self.hotkey.release(input_sender.keyboard.Key.shift_l, 0xA0)
        self.assertEqual(
            self.hotkey.press(input_sender.keyboard.Key.scroll_lock, 0x91),
            "clipboard",
        )

    def test_scroll_without_shift_preserves_remote_action(self):
        self.assertEqual(
            self.hotkey.press(input_sender.keyboard.Key.scroll_lock, 0x91),
            "remote",
        )

    def test_missed_scroll_release_recovers_on_next_physical_event(self):
        self.assertEqual(
            self.hotkey.press(input_sender.keyboard.Key.scroll_lock, 0x91),
            "remote",
        )
        # Simulate a lost release callback: a later unrelated event observes
        # that Scroll Lock is physically up and clears the shared latch.
        self.hotkey.press(input_sender.keyboard.Key.f1, 0x70)
        self.assertEqual(
            self.hotkey.press(input_sender.keyboard.Key.scroll_lock, 0x91),
            "remote",
        )

    def test_left_and_right_shift_events_keep_distinct_pairs(self):
        emitted = []
        original_hotkey = input_sender._clipboard_hotkey
        input_sender._clipboard_hotkey = self.hotkey
        input_sender.pressed_keys.clear()
        try:
            with patch.object(
                input_sender, "_emit", lambda raw, monitor=True: emitted.append(raw),
            ):
                self.down.add(0xA0)
                input_sender.on_press(input_sender.keyboard.Key.shift_l)
                self.down.add(0xA1)
                input_sender.on_press(input_sender.keyboard.Key.shift_r)
                self.down.remove(0xA0)
                input_sender.on_release(input_sender.keyboard.Key.shift_l)
                self.down.remove(0xA1)
                input_sender.on_release(input_sender.keyboard.Key.shift_r)
        finally:
            input_sender._clipboard_hotkey = original_hotkey
            input_sender.pressed_keys.clear()
        events = [json.loads(raw) for raw in emitted]
        self.assertEqual([event["type"] for event in events], [
            "key_down", "key_down", "key_up", "key_up",
        ])
        self.assertEqual([event["vk"] for event in events], [160, 161, 160, 161])


class FakePopup:
    instances = []

    def __init__(self):
        type(self).instances.append(self)
        self.shown = []
        self.hidden = 0
        self.stopped = False

    def start(self):
        pass

    def pump(self):
        pass

    def show(self, text):
        self.shown.append(text)

    def hide(self):
        self.hidden += 1

    def stop(self):
        self.stopped = True


class NotificationWindowTests(unittest.TestCase):
    def test_latest_notice_replaces_and_timer_hides_without_activation_api(self):
        FakePopup.instances.clear()
        notice = notification_window.NotificationWindow(
            backend_factory=FakePopup, duration=0.03,
        )
        self.assertTrue(notice.start())
        notice.show("old")
        notice.show("latest")
        time.sleep(0.1)
        notice.shutdown()
        popup = FakePopup.instances[0]
        self.assertEqual(popup.shown[-1], "latest")
        self.assertGreaterEqual(popup.hidden, 1)
        self.assertTrue(popup.stopped)
        self.assertEqual(notice.pending_count(), 0)

    def test_native_styles_are_nonactivating_and_click_through(self):
        self.assertTrue(notification_window.WS_EX_NOACTIVATE)
        self.assertTrue(notification_window.WS_EX_TOOLWINDOW)
        self.assertTrue(notification_window.WS_EX_TRANSPARENT)
        self.assertTrue(notification_window.SWP_NOACTIVATE)
        self.assertEqual(notification_window.HTTRANSPARENT, -1)


if __name__ == "__main__":
    unittest.main()
