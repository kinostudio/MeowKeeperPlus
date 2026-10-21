from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.repair_event_media_paths import repair


class EventPathRepairTests(unittest.TestCase):
    def test_verified_repair_backup_idempotency_and_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "events").mkdir()
            media = root / "events" / "event_good.mp4"
            media.write_bytes(b"original-video")
            old = "C:/old/data/events/event_good.mp4"
            with closing(sqlite3.connect(root / "meowkeeper.db")) as db, db:
                db.execute("CREATE TABLE media_events(event_id TEXT, clip_path TEXT, clip_sha256 TEXT, clip_size_bytes INTEGER, telegram_status TEXT)")
                db.execute("INSERT INTO media_events VALUES(?,?,?,?,?)", (
                    "good", old, hashlib.sha256(media.read_bytes()).hexdigest(), media.stat().st_size, "sent"))
            self.assertEqual(repair(root)["verified_candidates"], 1)
            with closing(sqlite3.connect(root / "meowkeeper.db")) as db:
                self.assertEqual(db.execute("SELECT clip_path FROM media_events").fetchone()[0], old)
            result = repair(root, apply=True)
            self.assertEqual(result["updated"], 1)
            with closing(sqlite3.connect(result["backup"])) as db:
                self.assertEqual(db.execute("SELECT clip_path FROM media_events").fetchone()[0], old)
            with closing(sqlite3.connect(root / "meowkeeper.db")) as db:
                self.assertEqual(db.execute("SELECT clip_path,telegram_status FROM media_events").fetchone(), (str(media), "sent"))
            self.assertEqual(repair(root, apply=True)["updated"], 0)

    def test_wrong_or_absent_hash_and_unsafe_id_are_never_changed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "events").mkdir()
            with closing(sqlite3.connect(root / "meowkeeper.db")) as db, db:
                db.execute("CREATE TABLE media_events(event_id TEXT, clip_path TEXT, clip_sha256 TEXT, clip_size_bytes INTEGER)")
                for name, digest in (("wrong", "0" * 64), ("unknown", None), ("../outside", "0" * 64)):
                    if "/" not in name:
                        (root / "events" / f"event_{name}.mp4").write_bytes(b"video")
                    db.execute("INSERT INTO media_events VALUES(?,?,?,?)", (name, "old.mp4", digest, 5))
            result = repair(root, apply=True)
            self.assertEqual(result["updated"], 0)
            self.assertEqual(result["mismatch"], 1)
            self.assertEqual(result["unverified"], 2)
            self.assertIsNone(result["backup"])
