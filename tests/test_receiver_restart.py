import sys
import unittest
from pathlib import Path
from unittest import mock

_RECEIVER_DIR = Path(__file__).resolve().parent.parent / "receiver"
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))

import input_server


class FakeThread:
    """Records what would have been threading.Thread(...) without running it,
    so tests can assert on start-count/target without ever restarting the
    real test process."""

    instances = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False
        FakeThread.instances.append(self)

    def start(self):
        self.started = True


class RestartRouteContractTests(unittest.TestCase):
    def setUp(self):
        FakeThread.instances = []
        with input_server._restart_lock:
            input_server._restart_pending = False
        self._thread_patch = mock.patch.object(input_server.threading, "Thread", FakeThread)
        self._thread_patch.start()
        self.addCleanup(self._thread_patch.stop)

    def test_post_restart_route_is_absent(self):
        self.assertNotIn("/api/restart", input_server._POST_ROUTES)

    def test_delete_restart_route_is_present(self):
        self.assertIn("/api/restart", input_server._DELETE_ROUTES)
        self.assertIs(input_server._DELETE_ROUTES["/api/restart"], input_server._api_delete_restart)

    def test_response_returns_before_worker_delay(self):
        # threading.Thread is faked to never actually run its target, so the
        # handler call below can only return promptly if it never blocks on
        # the 0.5s sleep inside _restart_server itself.
        result = input_server._api_delete_restart(None, b"")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(FakeThread.instances), 1)
        self.assertTrue(FakeThread.instances[0].started)
        self.assertIs(FakeThread.instances[0].target, input_server._restart_server)

    def test_repeated_calls_start_exactly_one_thread(self):
        for _ in range(5):
            result = input_server._api_delete_restart(None, b"")
            self.assertEqual(result, {"ok": True})
        self.assertEqual(len(FakeThread.instances), 1)


class RestartCleanupOrderingTests(unittest.TestCase):
    def setUp(self):
        with input_server._restart_lock:
            input_server._restart_pending = True
        self._orig_standalone_queue = input_server._standalone_queue
        self.addCleanup(self._restore_standalone_queue)

    def _restore_standalone_queue(self):
        input_server._standalone_queue = self._orig_standalone_queue

    def test_rc_and_standalone_cleanup_precede_exec_and_no_standalone_case(self):
        calls = []
        input_server._standalone_queue = None  # not standalone

        fake_standalone_module = mock.MagicMock()
        fake_standalone_module.stop = mock.MagicMock(
            side_effect=lambda: calls.append("standalone_stop"))

        with mock.patch.object(input_server.time, "sleep") as fake_sleep, \
             mock.patch.object(
                 input_server, "_set_rc_state",
                 side_effect=lambda enabled: calls.append(("rc", enabled))) as fake_rc, \
             mock.patch.dict(sys.modules, {"standalone_capture": fake_standalone_module}), \
             mock.patch.object(
                 input_server.os, "execv",
                 side_effect=lambda *a, **k: calls.append("execv")) as fake_execv:
            input_server._restart_server()

        fake_sleep.assert_called_once_with(0.5)
        fake_rc.assert_called_once_with(False)
        # Standalone was not active: standalone_capture.stop() must not run.
        fake_standalone_module.stop.assert_not_called()
        fake_execv.assert_called_once()
        self.assertEqual(calls, [("rc", False), "execv"])

    def test_standalone_cleanup_runs_before_exec_when_standalone_active(self):
        import asyncio
        calls = []
        input_server._standalone_queue = asyncio.Queue()

        fake_standalone_module = mock.MagicMock()
        fake_standalone_module.stop = mock.MagicMock(
            side_effect=lambda: calls.append("standalone_stop"))

        with mock.patch.object(input_server.time, "sleep"), \
             mock.patch.object(
                 input_server, "_set_rc_state",
                 side_effect=lambda enabled: calls.append(("rc", enabled))), \
             mock.patch.dict(sys.modules, {"standalone_capture": fake_standalone_module}), \
             mock.patch.object(
                 input_server.os, "execv",
                 side_effect=lambda *a, **k: calls.append("execv")) as fake_execv:
            input_server._restart_server()

        fake_standalone_module.stop.assert_called_once()
        fake_execv.assert_called_once()
        self.assertEqual(calls, [("rc", False), "standalone_stop", "execv"])

    def test_rc_cleanup_failure_does_not_skip_standalone_cleanup_or_exec(self):
        import asyncio
        calls = []
        input_server._standalone_queue = asyncio.Queue()

        fake_standalone_module = mock.MagicMock()
        fake_standalone_module.stop = mock.MagicMock(
            side_effect=lambda: calls.append("standalone_stop"))

        with mock.patch.object(input_server.time, "sleep"), \
             mock.patch.object(
                 input_server, "_set_rc_state",
                 side_effect=RuntimeError("boom")), \
             mock.patch.dict(sys.modules, {"standalone_capture": fake_standalone_module}), \
             mock.patch.object(
                 input_server.os, "execv",
                 side_effect=lambda *a, **k: calls.append("execv")) as fake_execv:
            input_server._restart_server()

        fake_standalone_module.stop.assert_called_once()
        fake_execv.assert_called_once()
        self.assertEqual(calls, ["standalone_stop", "execv"])

    def test_execv_failure_releases_guard_for_retry(self):
        with mock.patch.object(input_server.time, "sleep"), \
             mock.patch.object(input_server, "_set_rc_state"), \
             mock.patch.object(
                 input_server.os, "execv", side_effect=OSError("no exec")):
            input_server._restart_server()

        with input_server._restart_lock:
            self.assertFalse(input_server._restart_pending)


if __name__ == "__main__":
    unittest.main()
