import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.core.media.event_clip import EventClipRequest, EventClipService
from meowkeeper.core.media.media_validation import MediaValidationResult
from meowkeeper.core.notifications.telegram import TelegramSendResult
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore


class NotificationReceiptTests(unittest.TestCase):
    def test_health_reads_current_controller_and_handles_failure(self):
        status = Mock(return_value={"status": "running"})
        core = CoreStateFacade(callbacks={"status": status})
        self.assertEqual(core.health()["core_status"], "running")
        status.return_value = {"status": "error"}
        self.assertEqual(core.health()["core_status"], "error")
        status.side_effect = RuntimeError("private diagnostic")
        self.assertEqual(core.health(), {"status": "ok", "service": "MeowKeeper+", "core_status": "unknown"})

    def test_retry_does_not_duplicate_already_delivered_recipient(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.mp4"
            clip.write_bytes(b"test")
            notifier = Mock()
            notifier.send_video.side_effect = [
                TelegramSendResult("one", 11), RuntimeError("network"), TelegramSendResult("two", 12),
            ]
            store = Mock()
            service = EventClipService(Mock(), Mock(), output_directory=directory,
                                       store=store, notifier=notifier, chat_ids=["one", "two"],
                                       retry_sleep=lambda _: None)
            try:
                request = EventClipRequest("e", datetime.now(timezone.utc), "cat_detected", "local")
                validation = MediaValidationResult(
                    clip, True, True, False, 1.0,
                    video_codec="h264", width=320, height=240, pixel_format="yuv420p",
                    container_format="mov,mp4,m4a", mp4_faststart=True,
                )
                with patch("meowkeeper.core.media.event_clip.validate_media", return_value=validation):
                    attempts, error = service._send_with_retries(request, clip, "test")
                self.assertEqual((attempts, error), (2, None))
                self.assertEqual([c.args[0] for c in notifier.send_video.call_args_list], ["one", "two", "two"])
                self.assertEqual(store.record_telegram_receipt.call_count, 2)
            finally:
                service.close()

    def test_v3_database_upgrades_without_dropping_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.db"
            store = SQLiteStore(path)
            store.create_media_event(event_id="old", event_type="cat_detected",
                                     trigger_at=datetime.now(timezone.utc), source_id="local",
                                     track_id=None, pet_id="unknown", confidence=0.7,
                                     requested_pre_seconds=5, requested_post_seconds=10)
            store.connection.execute("DROP TABLE telegram_receipts")
            store.connection.execute("UPDATE schema_meta SET value='3' WHERE key='schema_version'")
            store.connection.execute("PRAGMA user_version=3")
            store.connection.commit()
            store.close()
            store = SQLiteStore(path)
            self.assertEqual(store.schema_version, SCHEMA_VERSION)
            self.assertIsNotNone(store.get_media_event("old"))
            store.record_telegram_receipt("old", "a" * 64, "video", 9)
            store.initialize()
            store.record_telegram_receipt("old", "a" * 64, "video", 9)
            self.assertEqual(len(store.telegram_receipts("old")), 1)
            with self.assertRaises(ValueError):
                store.record_telegram_receipt("old", "raw-chat-id", "video", 9)
            store.close()
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
