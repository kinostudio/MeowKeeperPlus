import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from meowkeeper.core.behavior.models import BehaviorEvent
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.media.event_clip import EventClipRequest, EventClipService
from meowkeeper.domain.behavior import BehaviorState, MotionState
from meowkeeper.storage.database import SQLiteStore


class MediaIdentityBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "events.db")
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def media(self, event_id="clip", track="session:1"):
        self.store.create_media_event(event_id=event_id, event_type="cat_detected", trigger_at=self.now,
                                      source_id="safe", track_id=track, pet_id=None, confidence=0.8,
                                      requested_pre_seconds=5, requested_post_seconds=10)

    def behavior(self):
        return BehaviorEvent(None, "session:1", BehaviorState.RESTING, MotionState.STATIONARY,
                             (), self.now, None, 0.8, None, "rule", "test", "test", "safe", None)

    def test_video_caption_uses_late_identity_display_name_not_trigger_snapshot(self):
        root = Path(self.temp.name)
        registry = PetRegistry(root / "pets")
        registry.register_pet("測試貓", pet_id="pet-a")
        service = object.__new__(EventClipService)
        service.output_directory = root / "events"
        service.store = self.store
        self.media()
        request = EventClipRequest("clip", self.now, "cat_detected", "safe", track_id="session:1")
        result = SimpleNamespace(actual_duration_seconds=15)
        self.assertIn("貓咪剛剛出現在畫面", service._caption(request, result, []))
        self.store.resolve_media_identity(track_id="session:1", pet_id="pet-a", confidence=.9, resolved_at=self.now)
        self.assertIn("測試貓（候選）剛剛出現在畫面", service._caption(request, result, []))
        mismatched = EventClipRequest("clip", self.now, "cat_detected", "safe", track_id="different-track")
        self.assertIn("貓咪剛剛出現在畫面", service._caption(mismatched, result, []))

    def test_late_identity_keeps_receipt_and_writes_one_audit(self):
        self.media()
        self.media("other", "another-session:1")
        self.store.record_telegram_receipt("clip", "a" * 64, "video", 42)
        args = {"track_id": "session:1", "pet_id": "pet-a", "confidence": 0.9, "resolved_at": self.now}
        self.assertEqual(self.store.resolve_media_identity(**args), 1)
        self.assertEqual(self.store.resolve_media_identity(**args), 0)
        self.assertEqual(self.store.get_media_event("clip")["pet_id"], "pet-a")
        self.assertEqual(self.store.get_media_event("other")["pet_id"], "unknown")
        self.assertEqual(self.store.telegram_receipts("clip")[0]["message_id"], 42)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM identity_corrections").fetchone()[0], 1)

    def test_identity_arrives_before_async_media_insert(self):
        self.store.save_track("session:1", source_id="safe", started_at=self.now, pet_id="pet-a")
        self.media()
        self.assertEqual(self.store.get_media_event("clip")["pet_id"], "pet-a")

    def test_human_review_survives_flush_and_blocks_automatic_patch(self):
        event = self.behavior()
        self.store.save_behavior_event(event)
        self.store.add_event_label(event.event_id, "confirmed")
        self.store.connection.execute("UPDATE behavior_events SET human_review=1,pet_id='human-pet' WHERE event_id=?", (event.event_id,))
        self.store.connection.commit()
        self.store.save_behavior_event(event)
        row = self.store.connection.execute("SELECT human_review,pet_id FROM behavior_events").fetchone()
        self.assertEqual(tuple(row), (1, "human-pet"))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM event_labels").fetchone()[0], 1)
        self.media()
        self.assertEqual(self.store.resolve_media_identity(track_id="session:1", pet_id="auto-pet", confidence=0.9, resolved_at=self.now), 0)
