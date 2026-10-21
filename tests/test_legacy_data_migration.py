from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.migrate_legacy_app_data import move_legacy_data, rollback_data


class LegacyInstalledDataMigrationTests(unittest.TestCase):
    def test_moves_user_data_and_config_and_can_roll_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_app = root / "App.upgrade-backup"
            source = old_app / "data"
            destination = root / "Data"
            journal = root / "migration.json"
            (source / "events").mkdir(parents=True)
            (source / "events" / "event_abc.mp4").write_bytes(b"video")
            (source / "pets" / "cat_alpha").mkdir(parents=True)
            (source / "pets" / "cat_alpha" / "profile.json").write_text("{}", encoding="utf-8")
            (source / "cache").mkdir()
            (source / "cache" / "temporary.bin").write_bytes(b"cache")
            (source / "locks").mkdir()
            (source / "locks" / "old.json").write_text("{}", encoding="utf-8")
            (old_app / "config.json").write_text('{"dashboard":{}}', encoding="utf-8")
            (destination / "install-history").mkdir(parents=True)
            (destination / "install-history" / "previous.json").write_text("{}", encoding="utf-8")

            moved = move_legacy_data(str(source), str(destination), str(journal), str(old_app / "config.json"))
            self.assertEqual(moved["status"], "passed")
            self.assertTrue((destination / "events" / "event_abc.mp4").is_file())
            self.assertTrue((destination / "pets" / "cat_alpha" / "profile.json").is_file())
            self.assertTrue((destination / "config.json").is_file())
            self.assertTrue((source / "cache" / "temporary.bin").is_file())
            self.assertTrue((source / "locks" / "old.json").is_file())
            self.assertTrue((destination / "install-history" / "previous.json").is_file())

            result = rollback_data(None, None, journal)
            self.assertEqual(result["status"], "passed")
            self.assertTrue((source / "events" / "event_abc.mp4").is_file())
            self.assertTrue((source / "pets" / "cat_alpha" / "profile.json").is_file())
            self.assertTrue((old_app / "config.json").is_file())
            self.assertFalse((destination / "events").exists())
            self.assertFalse((destination / "config.json").exists())

    def test_collision_stops_before_moving_anything(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "old" / "data"
            destination = root / "Data"
            journal = root / "migration.json"
            (source / "pets").mkdir(parents=True)
            (source / "pets" / "old.json").write_text("old", encoding="utf-8")
            (source / "events").mkdir()
            (source / "events" / "event_a.mp4").write_bytes(b"video")
            (destination / "pets").mkdir(parents=True)
            (destination / "pets" / "current.json").write_text("current", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "沒有覆寫任何資料"):
                move_legacy_data(str(source), str(destination), str(journal))
            self.assertTrue((source / "events" / "event_a.mp4").is_file())
            self.assertTrue((source / "pets" / "old.json").is_file())
            self.assertTrue((destination / "pets" / "current.json").is_file())
            self.assertFalse(journal.exists())


if __name__ == "__main__":
    unittest.main()
