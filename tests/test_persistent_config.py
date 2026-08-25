import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from input_common import persistent_config


class PersistentConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.legacy = self.root / "legacy"
        self.persistent = self.root / "persistent"

    def test_root_uses_override_then_localappdata(self):
        with mock.patch.dict(os.environ, {"INPUT_RELAY_CONFIG_DIR": r"X:\relay"}, clear=True):
            self.assertEqual(persistent_config.get_config_root(), Path(r"X:\relay"))
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"X:\AppData"}, clear=True):
            self.assertEqual(
                persistent_config.get_config_root(), Path(r"X:\AppData") / "InputRelay",
            )
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}, clear=True):
            self.assertEqual(
                persistent_config.get_config_root(), Path("/tmp/xdg") / "input-relay",
            )
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            Path, "home", return_value=Path("/home/tester"),
        ):
            self.assertEqual(
                persistent_config.get_config_root(),
                Path("/home/tester") / ".config" / "input-relay",
            )

    def test_migration_copies_all_files_once_without_touching_legacy(self):
        self.legacy.mkdir()
        expected = {}
        for index, filename in enumerate(persistent_config.CONFIG_FILENAMES):
            value = {"index": index}
            expected[filename] = json.dumps(value)
            (self.legacy / filename).write_text(expected[filename], encoding="utf-8")
        (self.persistent).mkdir()
        (self.persistent / "config.json").write_text('{"persistent": true}', encoding="utf-8")

        persistent_config.migrate_legacy_configs(self.legacy, self.persistent)

        self.assertEqual(
            (self.persistent / "config.json").read_text(encoding="utf-8"),
            '{"persistent": true}',
        )
        for filename in persistent_config.CONFIG_FILENAMES[1:]:
            self.assertEqual(
                (self.persistent / filename).read_text(encoding="utf-8"), expected[filename],
            )
        for filename, contents in expected.items():
            self.assertEqual((self.legacy / filename).read_text(encoding="utf-8"), contents)

    def test_atomic_writes_keep_bounded_previous_history(self):
        path = self.persistent / "config.json"
        for value in range(8):
            persistent_config.write_object_json(path, {"value": value})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": 7})
        backups = list((self.persistent / "backups").glob("config.json.*.bak"))
        self.assertEqual(len(backups), persistent_config.BACKUP_LIMIT)
        self.assertFalse(list((self.persistent / "backups").glob("*.tmp")))

    def test_corrupt_primary_recovers_newest_object_backup(self):
        path = self.persistent / "config.json"
        persistent_config.write_object_json(path, {"value": 1})
        persistent_config.write_object_json(path, {"value": 2})
        path.write_text("not json", encoding="utf-8")

        self.assertEqual(persistent_config.load_object_json(path, {"default": True}), {"value": 1})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": 1})

    def test_missing_or_non_object_primary_returns_default_without_backup(self):
        path = self.persistent / "config.json"
        path.parent.mkdir()
        path.write_text("[]", encoding="utf-8")
        self.assertEqual(persistent_config.load_object_json(path, {"default": True}), {"default": True})

    def test_initial_backup_is_retained_and_recovers_a_missing_primary(self):
        path = self.persistent / "config.json"
        baseline = self.persistent / "backups" / "config.json.initial.bak"
        baseline.parent.mkdir(parents=True)
        baseline.write_text('{"baseline": true}', encoding="utf-8")

        self.assertEqual(
            persistent_config.load_object_json(path, {"default": True}), {"baseline": True},
        )
        for value in range(8):
            persistent_config.write_object_json(path, {"value": value})
        self.assertTrue(baseline.exists())
        self.assertEqual(
            len(list(baseline.parent.glob("config.json.*.bak"))),
            persistent_config.BACKUP_LIMIT + 1,
        )

    def test_backup_wins_over_stale_legacy_when_primary_is_missing(self):
        self.legacy.mkdir()
        (self.legacy / "sender_config.json").write_text(
            '{"http_port": 8082}', encoding="utf-8",
        )
        backup = self.persistent / "backups" / "sender_config.json.initial.bak"
        backup.parent.mkdir(parents=True)
        backup.write_text('{"http_port": 9191}', encoding="utf-8")

        result = persistent_config.load_object_json(
            self.persistent / "sender_config.json", {}, legacy_config_dir=self.legacy,
        )

        self.assertEqual(result, {"http_port": 9191})
        self.assertEqual(
            json.loads((self.persistent / "sender_config.json").read_text(encoding="utf-8")),
            {"http_port": 9191},
        )


if __name__ == "__main__":
    unittest.main()
