"""Closed segment timing comes from muxer metadata, not target cut length."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meowkeeper.core.media.ring_buffer import DiskSegmentRingBuffer


class SegmentTimingTests(unittest.TestCase):
    def test_three_second_gop_selects_early_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "ingest_test"
            session.mkdir()
            end = datetime.now(timezone.utc) - timedelta(seconds=2)
            for i in range(2):
                path = session / f"segment_{i:08d}.ts"
                path.write_bytes(b"test")
                os.utime(path, (end.timestamp() + i * 3,) * 2)
            (session / "segments.csv").write_text("segment_00000000.ts,0.000,3.000\n", encoding="utf-8")
            ring = DiskSegmentRingBuffer(root, segment_seconds=1)
            ring.begin_session(session, realtime=True)
            ring.scan()
            records = ring.snapshot(0.1, now=end - timedelta(seconds=2.5))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].duration_seconds, 3)
            ring.scan()
            self.assertEqual(len(ring.index.all()), 1)

    def test_partial_and_invalid_rows_are_not_indexed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "segments.csv"
            manifest.write_text(
                "segment_00000000.ts,0,nan\n../segment_00000001.ts,0,3\n"
                "segment_00000002.ts,5,2\nsegment_00000003.ts,0,3",
                encoding="utf-8",
            )
            self.assertEqual(DiskSegmentRingBuffer._segment_durations(root), {})
            manifest.write_text("segment_00000003.ts,0,3\n", encoding="utf-8")
            self.assertEqual(DiskSegmentRingBuffer._segment_durations(root), {"segment_00000003.ts": 3})

    def test_missing_list_retains_legacy_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(DiskSegmentRingBuffer._segment_durations(Path(temporary)))

    def test_extreme_and_nonfinite_segment_durations_are_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "segments.csv"
            manifest.write_text(
                "segment_00000000.ts,0,86401\n"
                "segment_00000001.ts,0,1e309\n"
                "segment_00000002.ts,0,nan\n"
                "segment_00000003.ts,0,3\n",
                encoding="utf-8",
            )
            self.assertEqual(
                DiskSegmentRingBuffer._segment_durations(root),
                {"segment_00000003.ts": 3},
            )

    def test_registration_rejects_extreme_duration_and_timestamp_overflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ring = DiskSegmentRingBuffer(root / "ring")
            copied = ring.directory / "segment.ts"
            copied.write_bytes(b"segment")
            self.assertIsNone(ring.register_file(copied, duration_seconds=86401))
            self.assertIsNone(ring.register_file(copied, duration_seconds=float("inf")))
            self.assertIsNone(ring.register_file(
                copied,
                captured_at=datetime.max.replace(tzinfo=timezone.utc),
                duration_seconds=1,
            ))
            self.assertEqual(ring.index.all(), ())


if __name__ == "__main__":
    unittest.main()
