import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SENDER_DIR = Path(__file__).resolve().parent.parent / "sender"
if str(_SENDER_DIR) not in sys.path:
    sys.path.insert(0, str(_SENDER_DIR))

import monitor_ws


class FakeMonitorClient:
    """Stand-in for a monitor websocket client: no real socket. `gate`, if
    given, is an asyncio.Event that send() awaits before "completing" --
    used to simulate a stalled client. `close_gate`, if given, does the same
    for close() -- used to simulate a close() that never returns on its own."""

    def __init__(self, gate=None, close_gate=None):
        self.sent = []
        self.closed = False
        self._gate = gate
        self._close_gate = close_gate

    async def send(self, data):
        if self._gate is not None:
            await self._gate.wait()
        self.sent.append(data)

    async def close(self):
        if self._close_gate is not None:
            await self._close_gate.wait()
        self.closed = True


class EnqueueOverflowTests(unittest.TestCase):
    """The loop-thread enqueue helper: no clients means drop immediately;
    a full queue drops exactly the oldest item before enqueuing the newest."""

    def setUp(self):
        self.server = monitor_ws.MonitorServer(loop=None, is_running=lambda: True)

    def test_drops_immediately_when_no_clients(self):
        self.server._enqueue_on_loop("data")
        self.assertTrue(self.server._queue.empty())

    def test_drops_oldest_on_overflow_and_keeps_fifo(self):
        self.server._clients.add(object())
        maxsize = monitor_ws._QUEUE_MAXSIZE
        for i in range(maxsize):
            self.server._enqueue_on_loop(f"item-{i}")
        self.assertEqual(self.server._queue.qsize(), maxsize)

        self.server._enqueue_on_loop("newest")

        items = []
        while not self.server._queue.empty():
            items.append(self.server._queue.get_nowait())
        self.assertEqual(len(items), maxsize)
        self.assertNotIn("item-0", items)
        self.assertEqual(items[0], "item-1")  # oldest survivor, FIFO order preserved
        self.assertEqual(items[-1], "newest")


class BroadcastConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Per-client sends must run concurrently: a stalled client must not
    delay a healthy client's send, and the stalled client is dropped after
    its own bounded timeout."""

    async def test_stalled_client_does_not_delay_healthy_client(self):
        server = monitor_ws.MonitorServer(loop=asyncio.get_running_loop(), is_running=lambda: True)
        never = asyncio.Event()  # never set -> stalled client's send() never returns on its own
        stalled = FakeMonitorClient(gate=never)
        healthy = FakeMonitorClient()

        with patch.object(monitor_ws, "_CLIENT_SEND_TIMEOUT", 0.3):
            task = asyncio.ensure_future(asyncio.gather(
                server._send_to_client(stalled, "hello"),
                server._send_to_client(healthy, "hello"),
            ))
            await asyncio.sleep(0.05)
            # Healthy must have completed well before the stalled client's
            # 0.3s timeout has elapsed.
            self.assertEqual(healthy.sent, ["hello"])
            self.assertFalse(task.done())

            results = await asyncio.wait_for(task, timeout=2.0)

        self.assertIsNone(results[1])  # healthy: no drop
        self.assertIs(results[0], stalled)  # stalled: dropped after timeout

    async def test_broadcaster_closes_and_discards_only_failed_client(self):
        server = monitor_ws.MonitorServer(loop=asyncio.get_running_loop(), is_running=lambda: False)
        never = asyncio.Event()
        stalled = FakeMonitorClient(gate=never)
        healthy = FakeMonitorClient()
        server._clients.add(stalled)
        server._clients.add(healthy)
        await server._queue.put("payload")

        with patch.object(monitor_ws, "_CLIENT_SEND_TIMEOUT", 0.05):
            # Run exactly one broadcaster iteration's worth of work directly
            # (is_running() is False so _broadcaster's own while-loop would
            # exit immediately; we exercise its per-item body instead).
            data = await server._queue.get()
            clients = list(server._clients)
            results = await asyncio.gather(
                *(server._send_to_client(ws, data) for ws in clients)
            )
            for ws in results:
                if ws is None:
                    continue
                server._clients.discard(ws)
                await ws.close()

        self.assertEqual(healthy.sent, ["payload"])
        self.assertIn(healthy, server._clients)
        self.assertNotIn(stalled, server._clients)
        self.assertTrue(stalled.closed)
        self.assertFalse(healthy.closed)

    async def test_stalled_close_does_not_stop_broadcaster_or_healthy_delivery(self):
        """A dropped client whose close() itself never returns must not stall
        the broadcaster: the close is bounded by the same send timeout, and
        subsequent queue items keep being delivered to healthy clients."""
        calls = {"n": 0}

        def is_running():
            calls["n"] += 1
            return calls["n"] <= 2  # run exactly two broadcaster iterations

        server = monitor_ws.MonitorServer(loop=asyncio.get_running_loop(), is_running=is_running)
        never_send = asyncio.Event()      # never set -> send() never completes on its own
        never_close = asyncio.Event()     # never set -> close() never completes on its own
        failing = FakeMonitorClient(gate=never_send, close_gate=never_close)
        healthy = FakeMonitorClient()
        server._clients.add(failing)
        server._clients.add(healthy)
        await server._queue.put("first")
        await server._queue.put("second")

        with patch.object(monitor_ws, "_CLIENT_SEND_TIMEOUT", 0.05):
            start = asyncio.get_running_loop().time()
            await asyncio.wait_for(server._broadcaster(), timeout=2.0)
            elapsed = asyncio.get_running_loop().time() - start

        # Bounded: two send timeouts + one bounded (never-completing) close,
        # each capped at 0.05s, must finish in well under the 2.0s guard above.
        self.assertLess(elapsed, 1.0)
        self.assertFalse(failing.closed)  # close() itself never actually returned
        self.assertNotIn(failing, server._clients)
        self.assertEqual(healthy.sent, ["first", "second"])


if __name__ == "__main__":
    unittest.main()
