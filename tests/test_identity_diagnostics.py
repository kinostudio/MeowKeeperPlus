from __future__ import annotations

import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from meowkeeper.core.identity import BackgroundIdentityResolver, IdentityPrediction


class IdentityDiagnosticsTests(unittest.TestCase):
    def test_small_crop_reason_and_no_private_identifiers(self):
        import cv2
        import numpy as np

        resolver = BackgroundIdentityResolver(SimpleNamespace())
        try:
            _, jpeg = cv2.imencode(".jpg", np.zeros((61, 71, 3), dtype=np.uint8))
            self.assertFalse(resolver.submit(track_id="private-track", crop_jpeg=jpeg.tobytes(),
                                            captured_at=datetime.now(timezone.utc), source_id="private-source"))
            report = resolver.diagnostic_snapshot()
            self.assertEqual(report["stage"], "crop_too_small")
            self.assertEqual(report["quality_rejected"], 1)
            self.assertNotIn("private", str(report))
        finally:
            resolver.close(wait=True)

    def test_completed_unknown_reports_scores_without_names(self):
        class Classifier:
            embedder = SimpleNamespace(backend_name="CPU")

            def classify(self, crop, *, track_id):
                return IdentityPrediction(None, None, 0.33, 0.26, 0.07, 0.67, "test", track_id)

        resolver = BackgroundIdentityResolver(Classifier())
        try:
            self.assertTrue(resolver.submit(track_id="private-track",
                                            crop_jpeg=(Path(__file__).parent / "assets" / "cat.jpg").read_bytes(),
                                            captured_at=datetime.now(timezone.utc), source_id="private-source"))
            deadline = time.monotonic() + 2
            while resolver.pending_count and time.monotonic() < deadline:
                time.sleep(0.01)
            report = resolver.diagnostic_snapshot()
            self.assertEqual(report["stage"], "score_insufficient")
            self.assertEqual(report["completed"], 1)
            self.assertEqual(report["similarity"], 0.33)
            self.assertNotIn("private", str(report))
        finally:
            resolver.close(wait=True)


if __name__ == "__main__":
    unittest.main()
