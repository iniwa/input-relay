import sys
import tempfile
import unittest
from pathlib import Path

_RECEIVER_DIR = Path(__file__).resolve().parent.parent / "receiver"
if str(_RECEIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_RECEIVER_DIR))

import input_server


class PresetCrudTests(unittest.TestCase):
    """Exercise the pure preset load/save helpers against temp files only.

    PRESETS_PATH / LAYOUT_PRESETS_PATH are rebound for the duration of each
    test so real config/*.json is never touched.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)

        self._orig_presets_path = input_server.PRESETS_PATH
        self._orig_layout_presets_path = input_server.LAYOUT_PRESETS_PATH
        input_server.PRESETS_PATH = tmp / "presets.json"
        input_server.LAYOUT_PRESETS_PATH = tmp / "layout_presets.json"
        self.addCleanup(self._restore_paths)

    def _restore_paths(self):
        input_server.PRESETS_PATH = self._orig_presets_path
        input_server.LAYOUT_PRESETS_PATH = self._orig_layout_presets_path

    def test_load_presets_defaults_when_missing(self):
        self.assertEqual(
            input_server.load_presets(),
            {"keyboard": {}, "leverless": {}, "controller": {}},
        )

    def test_save_then_load_presets_round_trips(self):
        data = {"keyboard": {"my-preset": {"keyboard": {"a": "1"}}},
                "leverless": {}, "controller": {}}
        input_server.save_presets(data)
        self.assertEqual(input_server.load_presets(), data)

    def test_delete_preset_via_pop(self):
        data = {"keyboard": {"my-preset": {"keyboard": {}}},
                "leverless": {}, "controller": {}}
        input_server.save_presets(data)

        presets = input_server.load_presets()
        presets["keyboard"].pop("my-preset", None)
        input_server.save_presets(presets)

        self.assertEqual(input_server.load_presets()["keyboard"], {})

    def test_load_layout_presets_defaults_when_missing(self):
        self.assertEqual(
            input_server.load_layout_presets(),
            {"keyboard": {}, "leverless": {}, "controller": {}},
        )

    def test_save_then_load_layout_presets_round_trips(self):
        data = {"keyboard": {"my-layout": {"layout": {"x": 1}}},
                "leverless": {}, "controller": {}}
        input_server.save_layout_presets(data)
        self.assertEqual(input_server.load_layout_presets(), data)


if __name__ == "__main__":
    unittest.main()
