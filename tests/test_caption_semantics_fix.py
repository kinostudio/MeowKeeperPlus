"""Tests for Telegram caption phrasing accuracy and timestamp integrity."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from meowkeeper.core.event_semantics import SemanticResult
from meowkeeper.core.media.event_clip import (
    EventClipService,
    EventClipRequest,
    _format_natural_cat_headline,
)
from meowkeeper.core.media.media_validation import MediaValidationResult
from meowkeeper.core.media.ring_buffer import DiskSegmentRingBuffer
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.storage.database import SQLiteStore


class DummyNotifier:
    def __init__(self) -> None:
        self.sent_videos: list[dict] = []
        self.edited_captions: list[dict] = []

    def send_video(self, chat_id, data, *, caption, reply_markup=None, **kwargs):
        msg_id = len(self.sent_videos) + 100
        self.sent_videos.append({
            "chat_id": chat_id,
            "data": data,
            "caption": caption,
            "reply_markup": reply_markup,
            "message_id": msg_id,
        })
        return SimpleNamespace(message_id=msg_id)

    def edit_message_caption(self, chat_id, message_id, text, *, reply_markup=None):
        self.edited_captions.append({
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": text,
            "reply_markup": reply_markup,
        })


class CaptionSemanticsFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)
        self.events_dir = self.root / "events"
        self.events_dir.mkdir(parents=True)
        self.pets_dir = self.root / "pets"
        self.pets_dir.mkdir(parents=True)
        self.ring_dir = self.root / "ring"
        self.ring_dir.mkdir(parents=True)

        self._validation_patch = patch(
            "meowkeeper.core.media.event_clip.validate_media",
            side_effect=lambda path: MediaValidationResult(
                Path(path), True, True, False, 5.0,
                video_codec="h264", width=1920, height=1080, pixel_format="yuv420p",
                container_format="mov,mp4,m4a", mp4_faststart=True,
            ),
        )
        self._validation_patch.start()
        self.addCleanup(self._validation_patch.stop)

        reg = PetRegistry(self.pets_dir)
        reg.register_pet("嗨啵", pet_id="p_highball")
        reg.register_pet("Saki", pet_id="p_saki")

        self.db_path = self.root / "test.db"
        self.store = SQLiteStore(str(self.db_path))

        self.notifier = DummyNotifier()
        self.ring = DiskSegmentRingBuffer(self.ring_dir, retention_seconds=60)
        self.assembler = MagicMock()

        self.service = EventClipService(
            self.ring,
            self.assembler,
            output_directory=self.events_dir,
            store=self.store,
            notifier=self.notifier,
            chat_ids=("12345678",),
            telegram_enabled=True,
        )

    def tearDown(self) -> None:
        self.store.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scenario_1_playing_and_lying_caption_semantics(self) -> None:
        """Issue 1: '嗨啵剛剛出剛剛在玩耍、躺著' must be fixed to '嗨啵正在玩耍、躺著'."""
        headline = _format_natural_cat_headline("嗨啵", ["playing", "lying"])
        self.assertEqual(headline, "嗨啵正在玩耍、躺著。")
        self.assertNotIn("剛剛出", headline)
        self.assertNotIn("剛剛在", headline)

        # Merge candidate test
        initial_caption = "🐈 嗨啵剛剛出現在畫面。\n10/24 15:13｜影片 15:12:56～15:13:11\n喵喵守護者+ DINOv2辨識"
        semantics = SemanticResult(
            status="completed",
            tracks=(("trk-1", ("playing", "lying")),),
        )
        merged = self.service._merge_candidate_caption(
            initial_caption,
            semantics,
            pet_registry_dir=self.pets_dir,
            store=self.store,
            event_id="ev-1",
        )
        lines = merged.splitlines()
        self.assertEqual(lines[0], "🐈 嗨啵正在玩耍、躺著。")
        self.assertEqual(lines[1], "10/24 15:13｜影片 15:12:56～15:13:11")
        self.assertEqual(lines[2], "喵喵守護者+ 雲端辨識")

    def test_scenario_2_elevation_always_preserves_timestamp(self) -> None:
        """Issue 2: 'Saki現在乖乖坐著，神情好愜意～' must NOT lack timestamp."""
        now = datetime(2026, 10, 24, 7, 10, 0, tzinfo=timezone.utc)
        clip_start = datetime(2026, 10, 24, 7, 9, 50, tzinfo=timezone.utc)
        clip_end = datetime(2026, 10, 24, 7, 10, 5, tzinfo=timezone.utc)
        event_id = "ev-elevation-saki"

        self.store.create_media_event(
            event_id=event_id,
            event_type="cat_detected",
            trigger_at=now,
            source_id="cam-01",
            track_id="trk-saki",
            pet_id="unknown",
            confidence=0.88,
            requested_pre_seconds=5.0,
            requested_post_seconds=10.0,
            trigger_origin="detector",
        )
        self.store.update_media_event(
            event_id,
            clip_start_at=clip_start,
            clip_end_at=clip_end,
        )

        clip_path = self.events_dir / f"{event_id}.mp4"
        clip_path.write_bytes(b"dummy")

        req = EventClipRequest(
            event_id=event_id,
            trigger_at=now,
            event_type="cat_detected",
            source_id="cam-01",
            trigger_origin="detector",
            track_id="trk-saki",
        )
        self.service._send_with_retries(
            req, clip_path, "🐈 貓咪剛剛出現在畫面。\n10/24 15:10｜影片 15:09:50～15:10:05\n喵喵守護者+",
        )

        # Elevate to Saki sitting
        elevated = self.service.elevate_event_caption(
            event_id,
            pet_id="p_saki",
            posture="sitting",
        )
        self.assertTrue(elevated)
        self.assertEqual(len(self.notifier.edited_captions), 1)
        edited_caption = self.notifier.edited_captions[0]["caption"]
        lines = edited_caption.splitlines()

        self.assertIn("Saki", lines[0])
        self.assertIn("坐著", lines[0])
        # Timestamp must be present!
        self.assertEqual(lines[1], "10/24 15:10｜影片 15:09:50～15:10:05")
        self.assertEqual(lines[2], "喵喵守護者+ DINOv2辨識")

    def test_scenario_3_resting_caption_no_redundant_phrase(self) -> None:
        """Issue 3: '貓咪剛剛出現在正安靜地休息放空喔～' must not have redundant '剛剛出現在'."""
        initial_caption = "🐈 貓咪剛剛出現在畫面。\n10/24 08:30｜影片 08:30:18～08:30:33\n喵喵守護者+"
        semantics = SemanticResult(
            status="completed",
            tracks=(("trk-rest", ("resting",)),),
        )
        merged = self.service._merge_candidate_caption(
            initial_caption,
            semantics,
            pet_registry_dir=self.pets_dir,
            store=self.store,
            event_id="ev-rest",
        )
        lines = merged.splitlines()
        self.assertEqual(lines[0], "🐈 貓咪正安靜地休息放空喔～")
        self.assertNotIn("剛剛出現在", lines[0])
        self.assertEqual(lines[1], "10/24 08:30｜影片 08:30:18～08:30:33")
        self.assertEqual(lines[2], "喵喵守護者+ 雲端辨識")
