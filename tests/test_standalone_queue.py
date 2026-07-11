import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_RECEIVER_DIR = Path(__file__).resolve().parent.parent / "receiver"
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))

import input_server


class StandaloneQueueOverflowTests(unittest.TestCase):
    """_standalone_on_event: bounded to 500, normal FIFO, and on overflow
    the queued backlog is sacrificed for one input_reset plus the newest
    event -- sacrificing history is preferable to a missing key-up leaving
    the overlay stuck."""

    def setUp(self):
        self._orig_queue = input_server._standalone_queue
        self._orig_last_log = input_server._standalone_last_overflow_log
        input_server._standalone_queue = asyncio.Queue(maxsize=input_server._STANDALONE_QUEUE_MAXSIZE)
        input_server._standalone_last_overflow_log = 0.0
        self.addCleanup(self._restore)

    def _restore(self):
        input_server._standalone_queue = self._orig_queue
        input_server._standalone_last_overflow_log = self._orig_last_log

    def _drain(self):
        items = []
        while not input_server._standalone_queue.empty():
            items.append(input_server._standalone_queue.get_nowait())
        return items

    def test_noop_when_queue_not_set(self):
        input_server._standalone_queue = None
        input_server._standalone_on_event("x")  # must not raise

    def test_normal_operation_is_fifo(self):
        for i in range(5):
            input_server._standalone_on_event(f"event-{i}")
        self.assertEqual(self._drain(), [f"event-{i}" for i in range(5)])

    def test_overflow_clears_backlog_and_enqueues_reset_then_newest(self):
        maxsize = input_server._STANDALONE_QUEUE_MAXSIZE
        for i in range(maxsize):
            input_server._standalone_on_event(f"event-{i}")
        self.assertEqual(input_server._standalone_queue.qsize(), maxsize)

        input_server._standalone_on_event("newest")

        items = self._drain()
        self.assertEqual(len(items), 2)
        self.assertEqual(json.loads(items[0]), {"type": "input_reset"})
        self.assertEqual(items[1], "newest")

    def test_overflow_warning_log_is_rate_limited(self):
        maxsize = input_server._STANDALONE_QUEUE_MAXSIZE

        def fill(n):
            for i in range(n):
                input_server._standalone_on_event(f"e{i}")

        with patch.object(input_server.logger, "warning") as mock_warn:
            fill(maxsize)
            input_server._standalone_on_event("overflow-1")
            self.assertEqual(mock_warn.call_count, 1)
            self.assertEqual(input_server._standalone_queue.qsize(), 2)  # reset + newest

            # Refill to full and overflow again immediately: within the 5s
            # rate-limit window, so no new warning log.
            fill(maxsize - 2)
            input_server._standalone_on_event("overflow-2")
            self.assertEqual(mock_warn.call_count, 1)

            # Simulate that the rate-limit window has elapsed.
            input_server._standalone_last_overflow_log -= input_server._STANDALONE_OVERFLOW_LOG_INTERVAL
            fill(maxsize - 2)
            input_server._standalone_on_event("overflow-3")
            self.assertEqual(mock_warn.call_count, 2)


if __name__ == "__main__":
    unittest.main()
