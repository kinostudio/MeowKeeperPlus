"""雲端拼貼診斷、低頻收集與結果欄位不可在背景工作中遺失。"""

import inspect
import sqlite3
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from meowkeeper.core.event_semantics import (
    EventSemanticService,
    SemanticRequest,
    SemanticResult,
    TagEvidence,
)
from meowkeeper.core.events.v02_host_analyzer import V02HostAnalyzer
from meowkeeper.core.media.event_clip import EventClipService
from meowkeeper.core.semantic_storyboard import (
    FrameEvidence,
    FrameEvidenceBuffer,
    StoryboardEvidenceError,
    build_storyboards,
)
from meowkeeper.storage.semantic_jobs import restore_result
from tests.test_semantic_storyboard import jpeg


class CloudEvidenceWiringTests(unittest.TestCase):
    def setUp(self):
        self.trigger = datetime.now(timezone.utc)
        self.frames = tuple(FrameEvidence("track-a", "epoch", self.trigger + timedelta(seconds=i * .8), jpeg(i))
                            for i in range(4))
        self.boards = build_storyboards(self.frames, ("track-a",), self.trigger)
        self.request = SemanticRequest("event", b"jpeg", ("track-a",), self.trigger, self.boards, "epoch")

    def test_collection_budget_is_three_seconds_and_frame_window_is_enforced(self):
        self.assertEqual(inspect.signature(FrameEvidenceBuffer.collect).parameters["wait_seconds"].default, 3)
        buffer = FrameEvidenceBuffer()
        self.addCleanup(buffer.close)
        with self.assertRaises(ValueError):
            buffer.collect(("track-a",), self.trigger, wait_seconds=3.01)
        with self.assertRaises(ValueError):
            replace(self.boards[0], offsets_seconds=(0, .8, 1.6, 3.01))

    def test_low_fps_collection_keeps_four_frames_after_two_second_boundary(self):
        buffer = FrameEvidenceBuffer()
        self.addCleanup(buffer.close)
        result = []
        failures = []

        def collect():
            try:
                result.append(buffer.collect(("track-a",), self.trigger))
            except (ValueError, RuntimeError, OSError) as error:
                failures.append(error)

        worker = threading.Thread(target=collect)
        worker.start()
        try:
            for sample in self.frames:
                remaining = (sample.captured_at - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    time.sleep(remaining)
                buffer.observe((sample,))
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(result[0][0].offsets_seconds), 4)
            self.assertGreater(result[0][0].offsets_seconds[-1], 2)
        finally:
            buffer.close()
            worker.join(2)

    def test_diagnostics_separate_missing_images_track_and_invalid_content(self):
        cases = [
            (self.frames[:3], "insufficient_frames"),
            ((), "track_missing"),
            (tuple(replace(frame, epoch="other") if i == 1 else frame
                   for i, frame in enumerate(self.frames)), "track_changed"),
            (tuple(replace(frame, jpeg=b"not-a-jpeg") for frame in self.frames), "invalid_images"),
            (tuple(replace(frame, jpeg=jpeg(0)) for frame in self.frames), "duplicate_frames"),
            (tuple(replace(frame, captured_at=self.trigger - timedelta(seconds=10))
                   for frame in self.frames), "frame_window_expired"),
        ]
        for samples, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(StoryboardEvidenceError) as caught:
                build_storyboards(samples, ("track-a",), self.trigger, diagnose=True)
            self.assertEqual(caught.exception.reason, reason)
            self.assertNotIn("track-a", str(caught.exception))
        stale = replace(self.frames[0], epoch="stale", captured_at=self.trigger - timedelta(seconds=20))
        self.assertEqual(len(build_storyboards((*self.frames, stale), ("track-a",), self.trigger)), 1)

    def test_validator_separates_track_privacy_and_view_failures(self):
        analyzer = V02HostAnalyzer.__new__(V02HostAnalyzer)
        analyzer._semantic_privacy_blocked = False
        analyzer._load_saved_zones = False
        analyzer._latest_semantic_epoch = "epoch"
        self.assertTrue(analyzer.validate_semantic(self.request))
        for request, reason in [(replace(self.request, storyboards=()), "insufficient_frames"),
                                (replace(self.request, track_ids=("other",)), "track_mismatch")]:
            with self.assertRaises(StoryboardEvidenceError) as caught:
                analyzer.validate_semantic(request)
            self.assertEqual(caught.exception.reason, reason)
        analyzer._semantic_privacy_blocked = True
        with self.assertRaises(StoryboardEvidenceError) as caught:
            analyzer.validate_semantic(self.request)
        self.assertEqual(caught.exception.reason, "privacy_blocked")
        analyzer._semantic_privacy_blocked = False
        analyzer._latest_semantic_epoch = "new-epoch"
        with self.assertRaises(StoryboardEvidenceError) as caught:
            analyzer.validate_semantic(self.request)
        self.assertEqual(caught.exception.reason, "track_changed")
        analyzer._load_saved_zones = True
        analyzer.store = Mock()
        analyzer._zone_source = "safe-source"
        analyzer._scene_epoch = "view-epoch"
        with patch("meowkeeper.core.behavior.scene_views.SceneViewRegistry") as registry:
            registry.return_value.state.return_value = {"epoch": "new-view"}
            with self.assertRaises(StoryboardEvidenceError) as caught:
                analyzer.validate_semantic(self.request)
            self.assertEqual(caught.exception.reason, "view_changed")
            view = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            zoned = replace(self.request, storyboards=(replace(self.boards[0], zones=((0, view, "food", "food"),)),))
            registry.return_value.state.return_value = {"epoch": "view-epoch", "state": "unaligned", "active_view": None}
            with self.assertRaises(StoryboardEvidenceError) as caught:
                analyzer.validate_semantic(zoned)
            self.assertEqual(caught.exception.reason, "view_unaligned")
            registry.return_value.state.side_effect = sqlite3.OperationalError("private local detail")
            with self.assertRaises(StoryboardEvidenceError) as caught:
                analyzer.validate_semantic(self.request)
            self.assertEqual(caught.exception.reason, "view_unavailable")
            self.assertNotIn("private", str(caught.exception))

    def test_worker_preserves_caption_identity_and_history_roundtrip(self):
        identity = (("track-a", {"pet_id": "unknown", "certainty": "low", "reason": "fixture"}),)
        candidate = SemanticResult("completed", (("track-a", ("resting",)),),
                                   identity_candidates=identity, caption="貓咪正在休息。")
        raw = {"tracks": [{"track_alias": "track_01", "identity_candidate": identity[0][1],
                           "tags": [{"code": "resting", "certainty": "low", "evidence_frame_indices": [0, 3]}]}],
               "caption": candidate.caption, "summary": "discard arbitrary provider text"}
        provider = SimpleNamespace(provider_id="fixture", model_id="fixture", analyze=Mock(return_value=raw))
        service = EventSemanticService(provider)
        try:
            result = service.submit(self.request).result(2)
            self.assertEqual(result.caption, candidate.caption)
            self.assertEqual(result.identity_candidates, identity)
            recovered = restore_result(result.payload())
            self.assertEqual(recovered.caption, candidate.caption)
            self.assertEqual(recovered.identity_candidates, identity)
            self.assertNotIn("discard arbitrary", str(result.payload()))
        finally:
            service.close()

    def test_failure_reason_survives_worker_and_history_without_api_call(self):
        provider = SimpleNamespace(provider_id="fixture", model_id="fixture", analyze=Mock())

        def prepare(_request):
            raise StoryboardEvidenceError("insufficient_frames")

        service = EventSemanticService(provider, input_preparer=prepare)
        try:
            result = service.submit(replace(self.request, storyboards=())).result(2)
            self.assertEqual(result.status, "insufficient_evidence")
            self.assertEqual(result.evidence_reason, "insufficient_frames")
            self.assertFalse(result.request_dispatched)
            self.assertIn("圖片", result.payload()["evidence_message"])
            self.assertEqual(restore_result(result.payload()).evidence_reason, "insufficient_frames")
            provider.analyze.assert_not_called()
        finally:
            service.close()
        with self.assertRaises(ValueError):
            SemanticResult("insufficient_evidence", evidence_reason="private arbitrary text")

    def test_extended_observation_offsets_survive_tag_evidence_validation(self):
        evidence = TagEvidence("track-a", "resting", (0, 3), (0, 2.4), "low")
        result = SemanticResult("completed", (("track-a", ("resting",)),), job_id=str(uuid4()), evidence=(evidence,))
        self.assertEqual(restore_result(result.payload()).evidence, result.evidence)

    def test_cloud_caption_preserves_metadata_within_telegram_utf16_limit(self):
        result = SemanticResult("completed", (("track-a", ("resting",)),), caption="😺" * 1000)
        text = EventClipService._merge_candidate_caption("貓咪剛剛出現在畫面。\n11/04 20:00\n喵喵守護者+", result)
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 1024)
        self.assertIn("11/04 20:00", text)
        self.assertTrue(text.endswith("雲端辨識"))
