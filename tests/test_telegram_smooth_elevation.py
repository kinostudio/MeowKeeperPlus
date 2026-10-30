"""Unit tests for Telegram delayed identity auto-elevation and track smoothing."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

from meowkeeper.core.identity.base import IdentityPrediction, IdentityThresholds
from meowkeeper.core.identity.temporal_identity import TemporalIdentity
from meowkeeper.core.media.event_clip import EventClipService, EventClipRequest, _format_natural_cat_headline
from meowkeeper.core.media.ring_buffer import DiskSegmentRingBuffer
from meowkeeper.core.media.clip_assembler import ClipAssembler
from meowkeeper.core.media.media_validation import MediaValidationResult
from meowkeeper.core.event_semantics import SemanticResult
from meowkeeper.core.notifications.telegram import TelegramNotificationError, TelegramNotifier
from meowkeeper.core.tracking.base import BoundingBox, TrackObservation
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


class TelegramSmoothElevationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)
        self.events_dir = self.root / "events"
        self.events_dir.mkdir(parents=True)
        self.ring_dir = self.root / "ring"
        self.ring_dir.mkdir(parents=True)
        self.pets_dir = self.root / "pets"
        self.pets_dir.mkdir(parents=True)

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

        from meowkeeper.core.identity.pet_registry import PetRegistry
        reg = PetRegistry(self.pets_dir)
        reg.register_pet("嗨啵", pet_id="p_hiball")

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
        try:
            self.store.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_preflight_recheck_resolves_identity_before_dispatch(self) -> None:
        """Pillar 1: Pre-flight check updates caption right before send if DB resolved identity during transcode."""
        now = datetime.now(timezone.utc)
        event_id = "event-preflight-001"
        self.store.create_media_event(
            event_id=event_id,
            event_type="cat_detected",
            trigger_at=now,
            source_id="cam-01",
            track_id="trk-1",
            pet_id="unknown",
            confidence=0.9,
            requested_pre_seconds=2.0,
            requested_post_seconds=3.0,
            trigger_origin="detector",
        )

        clip_path = self.events_dir / f"{event_id}.mp4"
        clip_path.write_bytes(b"dummy-mp4-data")

        # Simulate background DINOv2 finishing while transcoding / queuing
        self.store.connection.execute(
            "UPDATE media_events SET pet_id='p_hiball' WHERE event_id=?",
            (event_id,),
        )
        self.store.connection.commit()

        request = EventClipRequest(
            event_id=event_id,
            trigger_at=now,
            event_type="cat_detected",
            source_id="cam-01",
            trigger_origin="detector",
            track_id="trk-1",
        )

        attempts, error = self.service._send_with_retries(
            request, clip_path, "🐈 貓咪剛剛出現在畫面。\n10/24 03:42｜喵喵守護者+",
        )
        self.assertEqual(error, None)
        self.assertEqual(len(self.notifier.sent_videos), 1)
        sent = self.notifier.sent_videos[0]
        self.assertIn("嗨啵", sent["caption"])
        self.assertIn("DINOv2辨識", sent["caption"])

    def test_post_delivery_caption_elevation(self) -> None:
        """Pillar 2: When DINOv2 completes after video is dispatched, edit_message_caption is invoked."""
        now = datetime.now(timezone.utc)
        event_id = "event-elevation-002"
        self.store.create_media_event(
            event_id=event_id,
            event_type="cat_detected",
            trigger_at=now,
            source_id="cam-01",
            track_id="trk-2",
            pet_id="unknown",
            confidence=0.9,
            requested_pre_seconds=2.0,
            requested_post_seconds=3.0,
            trigger_origin="detector",
        )

        clip_path = self.events_dir / f"{event_id}.mp4"
        clip_path.write_bytes(b"dummy-mp4-data")
        request = EventClipRequest(
            event_id=event_id,
            trigger_at=now,
            event_type="cat_detected",
            source_id="cam-01",
            trigger_origin="detector",
            track_id="trk-2",
        )

        attempts, error = self.service._send_with_retries(
            request, clip_path, "🐈 貓咪剛剛出現在畫面。\n10/24 03:42｜喵喵守護者+",
        )
        self.assertEqual(error, None)
        self.assertEqual(len(self.notifier.sent_videos), 1)
        msg_id = self.notifier.sent_videos[0]["message_id"]

        elevated = self.service.elevate_event_caption(
            event_id=event_id,
            pet_id="p_hiball",
            posture="sitting",
        )
        self.assertTrue(elevated)
        self.assertEqual(len(self.notifier.edited_captions), 1)
        edited = self.notifier.edited_captions[0]
        self.assertEqual(edited["message_id"], msg_id)
        self.assertIn("嗨啵", edited["caption"])
        self.assertIn("坐著", edited["caption"])
        self.assertIn("DINOv2辨識", edited["caption"])
        self.assertTrue(self.service.elevate_event_caption(
            event_id=event_id,
            pet_id="p_hiball",
            posture="sitting",
        ))
        self.assertEqual(len(self.notifier.edited_captions), 1)

    def _create_caption_receipt(self, event_id: str, track_id: str = "trk-caption") -> None:
        now = datetime.now(timezone.utc)
        self.store.create_media_event(
            event_id=event_id,
            event_type="cat_detected",
            trigger_at=now,
            source_id="cam-01",
            track_id=track_id,
            pet_id="unknown",
            confidence=0.9,
            requested_pre_seconds=2.0,
            requested_post_seconds=3.0,
            trigger_origin="detector",
        )
        self.store.record_telegram_receipt(
            event_id,
            hashlib.sha256(b"12345678").hexdigest(),
            "video",
            3580,
        )

    def test_repeated_caption_failure_is_throttled_and_safe_diagnostic_is_logged(self) -> None:
        self._create_caption_receipt("event-caption-failure")
        logs: list[str] = []
        self.service.logger = logs.append
        error = TelegramNotificationError(
            "safe generic exception",
            status_code=400,
            api_error_code=400,
            reason_code="bad_request",
        )
        self.notifier.edit_message_caption = MagicMock(side_effect=error)

        for _ in range(8):
            self.assertFalse(self.service.elevate_event_caption("event-caption-failure", "p_hiball"))

        self.notifier.edit_message_caption.assert_called_once()
        self.assertEqual(len(logs), 1)
        self.assertIn("Telegram API 400", logs[0])
        self.assertIn("已停止重複嘗試", logs[0])
        self.assertNotIn("safe generic exception", logs[0])

    def test_message_not_modified_is_idempotent_success(self) -> None:
        self._create_caption_receipt("event-caption-unchanged")
        logs: list[str] = []
        self.service.logger = logs.append
        self.notifier.edit_message_caption = MagicMock(
            side_effect=TelegramNotificationError(
                "safe generic exception",
                status_code=400,
                api_error_code=400,
                reason_code="message_not_modified",
            )
        )

        self.assertTrue(self.service.elevate_event_caption("event-caption-unchanged", "p_hiball"))
        self.assertTrue(self.service.elevate_event_caption("event-caption-unchanged", "p_hiball"))

        self.notifier.edit_message_caption.assert_called_once()
        self.assertEqual(logs, [])

    def test_retryable_caption_failure_uses_server_backoff_and_three_attempt_cap(self) -> None:
        self._create_caption_receipt("event-caption-retry")
        logs: list[str] = []
        self.service.logger = logs.append
        current_time = [100.0]
        self.service._monotonic = lambda: current_time[0]
        self.notifier.edit_message_caption = MagicMock(side_effect=TelegramNotificationError(
            "safe generic exception", api_error_code=429,
            reason_code="rate_limited", retry_after_seconds=5.0,
        ))

        for expected_attempt in (1, 2, 3):
            self.service.elevate_event_caption("event-caption-retry", "p_hiball")
            self.assertEqual(self.notifier.edit_message_caption.call_count, expected_attempt)
            self.service.elevate_event_caption("event-caption-retry", "p_hiball")
            self.assertEqual(self.notifier.edit_message_caption.call_count, expected_attempt)
            current_time[0] += 5.0

        for _ in range(5):
            self.service.elevate_event_caption("event-caption-retry", "p_hiball")
        self.assertEqual(self.notifier.edit_message_caption.call_count, 3)
        self.assertEqual(len(logs), 3)
        self.assertIn("Telegram API 429", logs[0])
        self.assertIn("停止重複更新", logs[-1])

    def test_telegram_api_error_metadata_is_classified_without_raw_description(self) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps({
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: message is not modified",
        }).encode("utf-8")
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with patch("meowkeeper.core.notifications.telegram.urlopen", return_value=response):
            with self.assertRaises(TelegramNotificationError) as caught:
                TelegramNotifier("123456:secret-token")._call("editMessageCaption", fields={})

        self.assertEqual(caught.exception.api_error_code, 400)
        self.assertEqual(caught.exception.reason_code, "message_not_modified")
        self.assertNotIn("Bad Request", str(caught.exception))

    def test_http_error_extracts_retry_after_without_exposing_body(self) -> None:
        body = json.dumps({
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests: retry later",
            "parameters": {"retry_after": 7},
        }).encode("utf-8")
        response_error = HTTPError(
            "https://api.telegram.org/bot123456:secret-token/editMessageCaption",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(body),
        )
        with patch("meowkeeper.core.notifications.telegram.urlopen", side_effect=response_error):
            with self.assertRaises(TelegramNotificationError) as caught:
                TelegramNotifier("123456:secret-token")._call("editMessageCaption", fields={})

        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.api_error_code, 429)
        self.assertEqual(caught.exception.reason_code, "rate_limited")
        self.assertEqual(caught.exception.retry_after_seconds, 7.0)
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertNotIn("Too Many Requests: retry later", str(caught.exception))

    def test_cloud_semantics_post_delivery_elevation(self) -> None:
        """Pillar 2: When cloud Multimodal AI returns after delivery, update caption silently."""
        now = datetime.now(timezone.utc)
        event_id = "event-semantics-003"
        self.store.create_media_event(
            event_id=event_id,
            event_type="cat_detected",
            trigger_at=now,
            source_id="cam-01",
            track_id="trk-3",
            pet_id="unknown",
            confidence=0.9,
            requested_pre_seconds=2.0,
            requested_post_seconds=3.0,
            trigger_origin="detector",
        )

        clip_path = self.events_dir / f"{event_id}.mp4"
        clip_path.write_bytes(b"dummy-mp4-data")
        request = EventClipRequest(
            event_id=event_id,
            trigger_at=now,
            event_type="cat_detected",
            source_id="cam-01",
            trigger_origin="detector",
            track_id="trk-3",
        )

        attempts, error = self.service._send_with_retries(
            request, clip_path, "🐈 貓咪剛剛出現在畫面。\n10/24 03:42｜喵喵守護者+",
        )
        self.assertEqual(error, None)

        semantics = SemanticResult(
            status="completed",
            tracks=(("trk-3", ("resting", "lying")),),
            caption="嗨啵現在躺在床上休息，好舒服～",
        )
        res = self.service.update_event_semantics(event_id, semantics)
        self.assertTrue(res)
        self.assertEqual(len(self.notifier.edited_captions), 1)
        edited = self.notifier.edited_captions[0]
        self.assertIn("嗨啵現在躺在床上休息，好舒服～", edited["caption"])
        self.assertIn("雲端辨識", edited["caption"])

    def test_track_stitching_temporal_identity(self) -> None:
        """Pillar 3: Track stitching preserves sample votes across small ID hops so valid_frames does not reset."""
        temporal = TemporalIdentity(thresholds=IdentityThresholds(temporal_vote_window=10))
        t0 = datetime(2026, 10, 24, 3, 29, 20, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=1.0)

        pred1 = IdentityPrediction(
            predicted_pet_id="p_hiball",
            predicted_name="嗨啵",
            similarity=0.82,
            second_best_similarity=0.57,
            confidence_margin=0.25,
            unknown_probability=0.1,
            model_version="dinov2",
            track_id="trk-15",
            stable=False,
            reason="第一影格",
        )

        res1 = temporal.update("trk-15", pred1, captured_at=t0, frame_id="f1")
        self.assertFalse(res1.stable)
        self.assertEqual(res1.valid_frames, 1)

        transferred = temporal.inherit_history("trk-15", "trk-19")
        self.assertTrue(transferred)

        pred2 = IdentityPrediction(
            predicted_pet_id="p_hiball",
            predicted_name="嗨啵",
            similarity=0.84,
            second_best_similarity=0.57,
            confidence_margin=0.27,
            unknown_probability=0.1,
            model_version="dinov2",
            track_id="trk-19",
            stable=False,
            reason="第二影格",
        )
        res2 = temporal.update("trk-19", pred2, captured_at=t1, frame_id="f2")
        self.assertTrue(res2.stable)
        self.assertEqual(res2.valid_frames, 2)
        self.assertEqual(res2.predicted_pet_id, "p_hiball")
        self.assertEqual(res2.predicted_name, "嗨啵")

    def test_native_4k_crop_rescaling_and_quality(self) -> None:
        """Pillar 4: Native 4K crop is rescaled if small (<96px) to meet quality filter threshold."""
        from meowkeeper.core.events.v02_pipeline import V02AnalysisPipeline
        from PIL import Image
        import io

        # Create dummy 4K frame (3840x2160)
        img = Image.new("RGB", (3840, 2160), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        native_bytes = buf.getvalue()

        pipeline = MagicMock(spec=V02AnalysisPipeline)
        pipeline.native_snapshot_provider = lambda: native_bytes
        pipeline._native_crop_cache = (0.0, None)
        # Call unbound method _get_native_crop
        obs = TrackObservation(
            track_id="trk-test",
            bbox=BoundingBox(100.0, 100.0, 130.0, 130.0), # 30x30 in 1920x1080 -> 60x60 in 3840x2160 (<96px)
            confidence=0.9,
            captured_at=datetime.now(timezone.utc),
            frame_width=1920,
            frame_height=1080,
            source_id="test-cam",
        )
        crop_bytes = V02AnalysisPipeline._get_native_crop(pipeline, obs)
        self.assertIsNotNone(crop_bytes)
        crop_img = Image.open(io.BytesIO(crop_bytes))
        # Verify it was smoothly rescaled to at least 96x96
        self.assertGreaterEqual(crop_img.width, 96)
        self.assertGreaterEqual(crop_img.height, 96)


if __name__ == "__main__":
    unittest.main()
