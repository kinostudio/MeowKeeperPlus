"""Saved/downloaded clips keep every frame, evenly timed, in write order.

Each synthetic frame's brightness equals its frame number, so a skipped,
duplicated or reordered frame shows up after the real recorder command and the
real clip assembler. No camera, network or user data is involved.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

from meowkeeper.core.media.clip_assembler import ClipAssembler
from meowkeeper.core.media.segment_index import SegmentRecord
from meowkeeper.core.media.segment_recorder import SegmentRecorder
from meowkeeper.runtime import bundled_binary

FFMPEG = bundled_binary("ffmpeg") or shutil.which("ffmpeg")
FFPROBE = bundled_binary("ffprobe") or shutil.which("ffprobe")
FPS = 20
FRAMES = 160


def _run(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=120, shell=False).stdout


@unittest.skipUnless(FFMPEG and FFPROBE, "需要 FFmpeg／FFprobe")
class SequentialClipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        source = self.root / "camera.ts"
        # Long GOP + B-frames + audio, like a real camera stream copied to TS.
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
              "-f", "lavfi", "-i", f"color=c=black:s=160x120:r={FPS},geq=lum='N':cb=128:cr=128",
              "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
              "-t", str(FRAMES / FPS), "-c:v", "libx264", "-qp", "0", "-g", "40", "-bf", "2", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "32k", "-output_ts_offset", "3600", "-f", "mpegts", str(source)])
        self.session = self.root / "session-0001"
        self.session.mkdir()
        recorder = SegmentRecorder(str(source), SimpleNamespace(directory=self.session),
                                   ffmpeg_path=FFMPEG, segment_seconds=1.0)
        recorder._session_dir = self.session
        command = recorder.build_command(str(source), stream_copy=True)
        _run(command)
        self.files = sorted(self.session.glob("segment_*.ts"))
        self.assertGreaterEqual(len(self.files), 4)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _records(self, *, invert: int | None = None) -> list[SegmentRecord]:
        base = datetime(2026, 11, 10, tzinfo=timezone.utc)
        records = [SegmentRecord(path=file, start_at=base + timedelta(seconds=2 * index), duration_seconds=2.0,
                                 size_bytes=file.stat().st_size) for index, file in enumerate(self.files)]
        if invert is not None:
            # Real-time sessions derive start_at from mtime - duration; a long
            # GOP segment can therefore look older than its predecessor.
            earlier = records[invert - 1].start_at - timedelta(milliseconds=200)
            records[invert] = SegmentRecord(path=records[invert].path, start_at=earlier, duration_seconds=3.2,
                                            size_bytes=records[invert].size_bytes)
        return records

    def _frame_brightness(self, clip: Path) -> list[int]:
        # Escape the Windows drive colon so the lavfi option parser does not
        # interpret it as a separator.
        movie_path = clip.as_posix().replace(":", r"\\:")
        output = _run([FFPROBE, "-v", "error", "-f", "lavfi", "-i",
                       f"movie={movie_path},signalstats", "-show_entries",
                       "frame_tags=lavfi.signalstats.YAVG", "-of", "json"])
        return [round(float(frame["tags"]["lavfi.signalstats.YAVG"])) for frame in json.loads(output)["frames"]]

    def _video_pts(self, clip: Path) -> list[float]:
        output = _run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                       "packet=pts_time", "-of", "json", str(clip)])
        return sorted(float(packet["pts_time"]) for packet in json.loads(output)["packets"])

    def _assert_sequential(self, clip: Path) -> None:
        brightness = self._frame_brightness(clip)
        self.assertEqual(len(brightness), FRAMES, "frames were dropped or duplicated")
        self.assertEqual(brightness, sorted(brightness), "frames are not in write order")
        self.assertEqual(len(set(brightness)), FRAMES, "a frame was duplicated")
        pts = self._video_pts(clip)
        gaps = [round(later - earlier, 4) for earlier, later in pairwise(pts)]
        self.assertTrue(all(abs(gap - 1 / FPS) < 0.002 for gap in gaps), f"uneven frame timing at boundaries: {max(gaps)}")

    def test_stream_copy_clip_has_every_frame_evenly_timed(self) -> None:
        result = ClipAssembler(ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE).assemble(self._records(), self.root / "clip.mp4")
        self.assertEqual(result.status, "completed", result.error)
        self.assertTrue(result.used_stream_copy)
        self._assert_sequential(result.path)

    def test_mtime_inverted_start_times_still_follow_write_order(self) -> None:
        result = ClipAssembler(ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE).assemble(self._records(invert=2), self.root / "clip.mp4")
        self.assertEqual(result.status, "completed", result.error)
        self._assert_sequential(result.path)

    def test_transcode_fallback_is_also_sequential(self) -> None:
        result = ClipAssembler(ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE).assemble(
            self._records(), self.root / "clip.mp4", prefer_stream_copy=False)
        self.assertEqual(result.status, "completed", result.error)
        self.assertFalse(result.used_stream_copy)
        brightness = self._frame_brightness(result.path)
        self.assertEqual(len(brightness), FRAMES)
        # Lossy re-encode: values stay ordered within a small tolerance.
        self.assertTrue(all(later >= earlier - 2 for earlier, later in pairwise(brightness)))

    def test_segment_filenames_order_numerically_across_sessions(self) -> None:
        base = datetime(2026, 11, 10, tzinfo=timezone.utc)
        older, newer = self.root / "s1", self.root / "s2"
        records = [SegmentRecord(path=newer / "segment_00000000.ts", start_at=base + timedelta(seconds=50)),
                   SegmentRecord(path=older / "segment_00000010.ts", start_at=base + timedelta(seconds=9)),
                   SegmentRecord(path=older / "segment_00000009.ts", start_at=base + timedelta(seconds=10)),
                   SegmentRecord(path=older / "segment_00000009.ts", start_at=base + timedelta(seconds=10))]
        ordered = ClipAssembler(ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE)._write_order(records)
        self.assertEqual([record.path.relative_to(self.root).as_posix() for record in ordered],
                         ["s1/segment_00000009.ts", "s1/segment_00000010.ts", "s2/segment_00000000.ts"])


if __name__ == "__main__":
    unittest.main()
