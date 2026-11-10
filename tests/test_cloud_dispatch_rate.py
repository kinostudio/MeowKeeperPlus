"""Cloud image analysis must not be dropped by short local pressure, brief visits or the notification cooldown."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

from meowkeeper.core.event_semantics import EventSemanticService, SemanticRequest
from meowkeeper.core.inference.adaptive_analyzer import AdaptiveAnalyzer
from meowkeeper.core.media.event_clip import EventClipRequest, EventClipService
from meowkeeper.core.semantic_storyboard import (
    FrameEvidence,
    FrameEvidenceBuffer,
    StoryboardEvidenceError,
)
from meowkeeper.storage.database import SQLiteStore


def jpeg(number: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (96, 96), (number * 40, 80, 90)).save(output, format="JPEG")
    return output.getvalue()


class CloudDispatchRateTests(unittest.TestCase):
    def test_short_media_pressure_is_waited_out_then_request_is_sent(self) -> None:
        calls = []
        provider = SimpleNamespace(analyze=lambda request, **_kw: calls.append(request) or
                                   {"tracks": [{"track_id": "scene", "tags": ["resting"]}]})
        service = EventSemanticService(provider)
        busy = {"until": 3}

        def probe():
            busy["until"] -= 1
            return "media_preparing" if busy["until"] > 0 else None

        try:
            service.pressure_probe = probe
            result = service.submit(SemanticRequest("event", b"jpeg")).result(10)
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(calls), 1)
        finally:
            service.close()

    def test_persistent_pressure_still_skips_without_sending(self) -> None:
        provider = SimpleNamespace(analyze=lambda *_a, **_k: self.fail("不應外送"))
        service = EventSemanticService(provider)
        try:
            service.pressure_probe = lambda: "thermal_pressure"
            service.pressure_wait_seconds = 0.6
            result = service.submit(SemanticRequest("event", b"jpeg")).result(10)
            self.assertEqual(result.status, "skipped_pressure")
            self.assertFalse(result.request_dispatched)
        finally:
            service.close()

    def test_normal_ai_load_does_not_block_cloud_requests(self) -> None:
        analyzer = AdaptiveAnalyzer(SimpleNamespace(inference_backend="CPU"), fps=5, logger=lambda _message: None)
        analyzer._temperature = None
        analyzer._pressure = False
        analyzer._load = 70
        self.assertIsNone(analyzer.cloud_pressure_reason())
        analyzer._load = 90
        self.assertEqual(analyzer.cloud_pressure_reason(), "compute_pressure")

    def test_brief_visit_builds_a_smaller_board_after_the_window(self) -> None:
        trigger = datetime.now(timezone.utc)
        buffer = FrameEvidenceBuffer()
        buffer.observe(tuple(FrameEvidence("t", "epoch", trigger + timedelta(seconds=index * .4), jpeg(index))
                             for index in range(2)))
        boards = buffer.collect(("t",), trigger, wait_seconds=0.3)
        self.assertEqual(len(boards[0].offsets_seconds), 2)
        single = FrameEvidenceBuffer()
        single.observe((FrameEvidence("t", "epoch", trigger, jpeg(1)),))
        with self.assertRaises(StoryboardEvidenceError) as caught:
            single.collect(("t",), trigger, wait_seconds=0.3)
        self.assertEqual(caught.exception.reason, "insufficient_frames")

    def test_full_board_is_still_preferred_when_frames_arrive(self) -> None:
        trigger = datetime.now(timezone.utc)
        buffer = FrameEvidenceBuffer()
        buffer.observe(tuple(FrameEvidence("t", "epoch", trigger + timedelta(seconds=index * .3), jpeg(index))
                             for index in range(4)))
        self.assertEqual(len(buffer.collect(("t",), trigger, wait_seconds=1)[0].offsets_seconds), 4)

    def test_cat_event_inside_notification_cooldown_still_gets_cloud_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = SQLiteStore(root / "db.sqlite")
            semantic = Mock()
            ring = Mock()
            ring.status.return_value = SimpleNamespace(disk_below_minimum=False)
            ring.retention_seconds = 60
            service = EventClipService(ring, Mock(), output_directory=root / "events", store=store,
                                       semantic_service=semantic, telegram_enabled=False,
                                       segment_finalize_wait_seconds=0)
            try:
                now = datetime.now(timezone.utc)
                service._last_by_type["cat_detected"] = now
                request = EventClipRequest("cooldown-event", now + timedelta(seconds=30), "cat_detected", "source",
                                           track_ids=("track-1",), screenshot_bytes=b"jpeg",
                                           pre_seconds=0, post_seconds=0)
                self.assertFalse(service.submit_event(request))
                self.assertEqual(store.get_media_event("cooldown-event")["clip_status"], "merged_cooldown")
                semantic.submit.assert_called_once()
                self.assertEqual(semantic.submit.call_args.args[0].event_id, "cooldown-event")
                ring.acquire_window.assert_not_called()
            finally:
                service.close() if callable(getattr(service, "close", None)) else None
                store.close()


if __name__ == "__main__":
    unittest.main()
