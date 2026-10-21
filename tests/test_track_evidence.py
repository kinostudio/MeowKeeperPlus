from __future__ import annotations

import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from meowkeeper.core.notifications.track_evidence import numbered_track_sheet
from meowkeeper.core.tracking.base import BoundingBox, TrackObservation


class TrackEvidenceTests(unittest.TestCase):
    def observation(self, payload):
        return TrackObservation(
            track_id="private-id", bbox=BoundingBox(0, 0, 20, 20),
            captured_at=datetime.now(timezone.utc), confidence=0.9,
            source_id="private-source", frame_width=640, frame_height=360,
            crop_jpeg=payload,
        )

    def test_missing_and_corrupt_images_do_not_create_false_evidence(self):
        self.assertIsNone(numbered_track_sheet([]))
        self.assertIsNone(numbered_track_sheet([self.observation(None)]))
        self.assertIsNone(numbered_track_sheet([self.observation(b"not-jpeg")]))

    def test_two_tracks_and_missing_crop_keep_numbered_slots(self):
        payload = (Path(__file__).parent / "assets" / "cat.jpg").read_bytes()
        result = numbered_track_sheet([
            self.observation(payload), self.observation(None), self.observation(payload),
        ])
        self.assertIsNotNone(result)
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.size, (768, 216))
            self.assertEqual(image.format, "JPEG")
            self.assertTrue(all(
                abs(actual - expected) <= 2
                for actual, expected in zip(image.getpixel((384, 100)), (238, 231, 220), strict=True)
            ))

    def test_sheet_has_bounded_dimensions(self):
        payload = (Path(__file__).parent / "assets" / "cat.jpg").read_bytes()
        result = numbered_track_sheet([self.observation(payload)] * 100)
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.size, (1024, 1080))
