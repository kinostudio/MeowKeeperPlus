import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meowkeeper.core.inference.live_overlay import LIVE_OVERLAY_TTL_SECONDS, update_live_overlay


class LiveOverlayTests(unittest.TestCase):
    def test_positive_detection_survives_an_empty_sample_until_frontend_ttl(self):
        captured_at = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        detections = [{"bbox": [0.5, 0.08, 0.6, 0.19], "confidence": 0.68}]
        positive = update_live_overlay(
            None, detections, source_epoch="camera-session-1", captured_at=captured_at,
        )

        retained = update_live_overlay(
            positive, [], source_epoch="camera-session-1",
            captured_at=captured_at + timedelta(seconds=1),
        )

        self.assertEqual(retained, positive)

    def test_empty_sample_expires_old_or_cross_epoch_detection(self):
        captured_at = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        positive = update_live_overlay(
            None, [{"bbox": [0.5, 0.08, 0.6, 0.19]}],
            source_epoch="camera-session-1", captured_at=captured_at,
        )

        expired = update_live_overlay(
            positive, [], source_epoch="camera-session-1",
            captured_at=captured_at + timedelta(seconds=2.01),
        )
        new_epoch = update_live_overlay(
            positive, [], source_epoch="camera-session-2",
            captured_at=captured_at + timedelta(milliseconds=500),
        )

        self.assertIsNone(expired)
        self.assertIsNone(new_epoch)

    def test_positive_sample_replaces_previous_and_is_bounded(self):
        captured_at = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        old = {"source_epoch": "camera-session-1", "captured_at": captured_at.isoformat(),
               "detections": [{"id": "old"}]}
        new_detections = [{"id": str(index)} for index in range(25)]

        updated = update_live_overlay(
            old, new_detections, source_epoch="camera-session-1",
            captured_at=captured_at + timedelta(milliseconds=500),
        )

        self.assertEqual(updated["detections"], new_detections[:20])
        self.assertEqual(updated["captured_at"], (captured_at + timedelta(milliseconds=500)).isoformat())

    def test_backend_retention_matches_frontend_expiry(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "src/meowkeeper/app/web/static/js/ai-overlay.js"
        ).read_text(encoding="utf-8")
        self.assertIn(f"age > {LIVE_OVERLAY_TTL_SECONDS:g}", source)


if __name__ == "__main__":
    unittest.main()
