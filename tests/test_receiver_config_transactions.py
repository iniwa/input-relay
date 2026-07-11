import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_RECEIVER_DIR = Path(__file__).resolve().parent.parent / "receiver"
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))

import input_server


class FakeHandler:
    """Minimal stand-in for OverlayHandler, only exposing what
    `_client_label` and the API handlers read."""

    def __init__(self):
        self.client_address = ("127.0.0.1", 12345)
        self.headers = {}


class AtomicWriteJsonTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)

    def test_round_trip_and_no_leftover_temp_files(self):
        target = self.dir / "data.json"
        input_server._atomic_write_json(target, {"a": 1})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1})
        leftovers = [p for p in self.dir.iterdir() if p.name != "data.json"]
        self.assertEqual(leftovers, [])

    def test_temp_file_is_created_in_target_directory(self):
        target = self.dir / "data.json"
        seen_tmp_dirs = []
        orig_replace = input_server.os.replace

        def spy_replace(src, dst):
            seen_tmp_dirs.append(Path(src).parent)
            return orig_replace(src, dst)

        with mock.patch.object(input_server.os, "replace", side_effect=spy_replace):
            input_server._atomic_write_json(target, {"a": 1})
        self.assertEqual(seen_tmp_dirs, [self.dir])

    def test_temp_file_removed_on_replace_failure(self):
        target = self.dir / "data.json"
        with mock.patch.object(
            input_server.os, "replace", side_effect=OSError("boom"),
        ):
            with self.assertRaises(OSError):
                input_server._atomic_write_json(target, {"a": 1})
        leftovers = list(self.dir.iterdir())
        self.assertEqual(leftovers, [])


class InjectWsPortTests(unittest.TestCase):
    def test_injects_actual_integer_port_into_head(self):
        html = "<html><head><title>x</title></head><body></body></html>"
        out = input_server._inject_ws_port(html, 8888)
        self.assertIn(
            "<head><script>window.__WS_PORT__=8888;</script><title>x</title></head>",
            out,
        )

    def test_uses_int_conversion_not_raw_value(self):
        html = "<head></head>"
        out = input_server._inject_ws_port(html, "9999")
        self.assertIn("window.__WS_PORT__=9999;", out)
        self.assertNotIn('"9999"', out)

    def test_only_first_head_tag_is_touched(self):
        html = "<head></head><head></head>"
        out = input_server._inject_ws_port(html, 1234)
        self.assertEqual(out.count("__WS_PORT__"), 1)


class SenderConfigMergeTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._orig_config_dir = input_server.CONFIG_DIR
        input_server.CONFIG_DIR = Path(self._tmpdir.name)
        self.addCleanup(self._restore_config_dir)
        self._broadcast_patch = mock.patch.object(input_server, "_broadcast_change")
        self._broadcast_patch.start()
        self.addCleanup(self._broadcast_patch.stop)

    def _restore_config_dir(self):
        input_server.CONFIG_DIR = self._orig_config_dir

    def _cfg_path(self):
        return input_server.CONFIG_DIR / "sender_config.json"

    def test_creates_file_with_only_host_and_port_when_missing(self):
        result = input_server._api_post_sender_config(
            FakeHandler(), json.dumps({"host": "192.168.1.211", "port": 8888}),
        )
        self.assertEqual(result, {"ok": True})
        saved = json.loads(self._cfg_path().read_text(encoding="utf-8"))
        self.assertEqual(saved, {"host": "192.168.1.211", "port": 8888})

    def test_merge_preserves_other_existing_keys(self):
        self._cfg_path().write_text(
            json.dumps({
                "host": "old-host", "port": 1111,
                "local_name": "Main PC", "http_port": 8082,
            }),
            encoding="utf-8",
        )
        input_server._api_post_sender_config(
            FakeHandler(), json.dumps({"host": "new-host", "port": 2222}),
        )
        saved = json.loads(self._cfg_path().read_text(encoding="utf-8"))
        self.assertEqual(saved, {
            "host": "new-host", "port": 2222,
            "local_name": "Main PC", "http_port": 8082,
        })

    def test_extra_incoming_keys_are_ignored(self):
        self._cfg_path().write_text(json.dumps({"host": "h", "port": 1}), encoding="utf-8")
        input_server._api_post_sender_config(
            FakeHandler(),
            json.dumps({"host": "h2", "port": 2, "http_port": 9999, "junk": True}),
        )
        saved = json.loads(self._cfg_path().read_text(encoding="utf-8"))
        self.assertEqual(saved, {"host": "h2", "port": 2})

    def test_broadcast_receives_merged_full_object(self):
        self._cfg_path().write_text(
            json.dumps({"host": "h", "port": 1, "local_name": "Main PC"}),
            encoding="utf-8",
        )
        with mock.patch.object(input_server, "_broadcast_change") as fake_broadcast:
            input_server._api_post_sender_config(
                FakeHandler(), json.dumps({"host": "h2", "port": 2}),
            )
        fake_broadcast.assert_called_once_with(
            "sender_config", {"data": {"host": "h2", "port": 2, "local_name": "Main PC"}},
        )


class _TwoThreadTransactionTestBase(unittest.TestCase):
    """Shared harness: patches the module-level save_* function so its first
    call blocks (holding `_config_io_lock`, acquired by the outer POST/DELETE
    transaction) until explicitly released, letting a second thread's attempt
    to enter the same transaction be proven blocked *before* release."""

    def _run_first_blocks_second(self, save_attr, first_call, second_call):
        entered = threading.Event()
        release = threading.Event()
        orig_save = getattr(input_server, save_attr)
        call_count = {"n": 0}

        def blocking_save(data):
            call_count["n"] += 1
            if call_count["n"] == 1:
                entered.set()
                self.assertTrue(release.wait(timeout=5), "release was never signaled")
            orig_save(data)

        with mock.patch.object(input_server, save_attr, side_effect=blocking_save):
            t1 = threading.Thread(target=first_call)
            t1.start()
            self.assertTrue(entered.wait(timeout=5), "first thread never entered transaction")

            t2 = threading.Thread(target=second_call)
            t2.start()
            # t2 must be provably blocked on _config_io_lock (held by t1's
            # in-progress transaction), not merely "probably slower": as long
            # as release is unset, t1 cannot exit its `with _config_io_lock`
            # block, so t2 cannot acquire it regardless of how long we wait.
            t2.join(timeout=0.3)
            self.assertTrue(t2.is_alive(), "second thread completed before first released the lock")

            release.set()
            t1.join(timeout=5)
            t2.join(timeout=5)
            self.assertFalse(t1.is_alive())
            self.assertFalse(t2.is_alive())


class PresetTransactionTests(_TwoThreadTransactionTestBase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)
        self._orig_presets_path = input_server.PRESETS_PATH
        input_server.PRESETS_PATH = tmp / "presets.json"
        self.addCleanup(self._restore)
        self._broadcast_patch = mock.patch.object(input_server, "_broadcast_change")
        self._broadcast_patch.start()
        self.addCleanup(self._broadcast_patch.stop)
        input_server.save_presets({
            "keyboard": {"existing": {"keyboard": {}}}, "leverless": {}, "controller": {},
        })

    def _restore(self):
        input_server.PRESETS_PATH = self._orig_presets_path

    def test_concurrent_saves_do_not_lose_updates(self):
        def save_a():
            input_server._api_post_presets(
                FakeHandler(), json.dumps({"type": "keyboard", "name": "a", "keyboard": {}}),
            )

        def save_b():
            input_server._api_post_presets(
                FakeHandler(), json.dumps({"type": "keyboard", "name": "b", "keyboard": {}}),
            )

        self._run_first_blocks_second("save_presets", save_a, save_b)

        final = input_server.load_presets()
        self.assertIn("a", final["keyboard"])
        self.assertIn("b", final["keyboard"])
        self.assertIn("existing", final["keyboard"])

    def test_concurrent_save_and_delete_do_not_interfere(self):
        def save_new():
            input_server._api_post_presets(
                FakeHandler(), json.dumps({"type": "keyboard", "name": "new", "keyboard": {}}),
            )

        def delete_existing():
            input_server._api_delete_presets(
                FakeHandler(), json.dumps({"type": "keyboard", "name": "existing"}),
            )

        self._run_first_blocks_second("save_presets", save_new, delete_existing)

        final = input_server.load_presets()
        self.assertIn("new", final["keyboard"])
        self.assertNotIn("existing", final["keyboard"])


class LayoutPresetTransactionTests(_TwoThreadTransactionTestBase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)
        self._orig_path = input_server.LAYOUT_PRESETS_PATH
        input_server.LAYOUT_PRESETS_PATH = tmp / "layout_presets.json"
        self.addCleanup(self._restore)
        self._broadcast_patch = mock.patch.object(input_server, "_broadcast_change")
        self._broadcast_patch.start()
        self.addCleanup(self._broadcast_patch.stop)
        input_server.save_layout_presets({
            "keyboard": {"existing": {"layout": {}}}, "leverless": {}, "controller": {},
        })

    def _restore(self):
        input_server.LAYOUT_PRESETS_PATH = self._orig_path

    def test_concurrent_saves_do_not_lose_updates(self):
        def save_a():
            input_server._api_post_layout_presets(
                FakeHandler(), json.dumps({"type": "keyboard", "name": "a"}),
            )

        def save_b():
            input_server._api_post_layout_presets(
                FakeHandler(), json.dumps({"type": "keyboard", "name": "b"}),
            )

        self._run_first_blocks_second("save_layout_presets", save_a, save_b)

        final = input_server.load_layout_presets()
        self.assertIn("a", final["keyboard"])
        self.assertIn("b", final["keyboard"])
        self.assertIn("existing", final["keyboard"])

    def test_concurrent_save_and_delete_do_not_interfere(self):
        def save_new():
            input_server._api_post_layout_presets(
                FakeHandler(), json.dumps({"type": "keyboard", "name": "new"}),
            )

        def delete_existing():
            input_server._api_delete_layout_presets(
                FakeHandler(), json.dumps({"type": "keyboard", "name": "existing"}),
            )

        self._run_first_blocks_second("save_layout_presets", save_new, delete_existing)

        final = input_server.load_layout_presets()
        self.assertIn("new", final["keyboard"])
        self.assertNotIn("existing", final["keyboard"])


if __name__ == "__main__":
    unittest.main()
