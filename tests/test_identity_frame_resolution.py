from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from meowkeeper.app.source_config import load_runtime_settings, save_runtime_settings
from meowkeeper.app.web.contracts import SettingsValidationError, validate_settings_patch
from meowkeeper.core.media import DiskSegmentRingBuffer, FfmpegMediaIngest
from meowkeeper.runtime import bundled_binary


class IdentityFrameResolutionTests(unittest.TestCase):
    def test_width_setting_roundtrip_and_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertEqual(load_runtime_settings(path)["identity_frame_width"], 1280)
            self.assertEqual(load_runtime_settings(path)["preview_frame_width"], 1920)
            save_runtime_settings(path, validate_settings_patch({"preview_frame_width": 1920, "analysis_width": 1280}))
            self.assertEqual(load_runtime_settings(path)["preview_frame_width"], 1920)
            self.assertEqual(load_runtime_settings(path)["analysis_width"], 1280)
            for value in (639, 1921, 960.5, True):
                with self.assertRaises(SettingsValidationError):
                    validate_settings_patch({"preview_frame_width": value})
            for value in (639, 641, 1080, 1921, True):
                with self.assertRaises(SettingsValidationError):
                    validate_settings_patch({"analysis_width": value})
            for width in (640, 960, 1280):
                values = validate_settings_patch({"identity_frame_width": width})
                save_runtime_settings(path, values)
                self.assertEqual(load_runtime_settings(path)["identity_frame_width"], width)
            for width in (640, 960, 1280, 1920):
                values = validate_settings_patch({"analysis_width": width})
                save_runtime_settings(path, values)
                self.assertEqual(load_runtime_settings(path)["analysis_width"], width)
            for value in (639, 1281, 960.5, True):
                with self.assertRaises(SettingsValidationError):
                    validate_settings_patch({"identity_frame_width": value})
                with self.assertRaises(ValueError):
                    save_runtime_settings(path, {"identity_frame_width": value})

    def test_single_input_and_low_cost_recording_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            ring = DiskSegmentRingBuffer(Path(directory) / "ring", retention_seconds=60)
            ingest = FfmpegMediaIngest("local.mp4", ring, width=1280)
            command = ingest.build_command("local.mp4", stream_copy=False)
            self.assertEqual(command.count("-i"), 1)
            self.assertEqual(command.count("pipe:1"), 1)
            self.assertIn("fps=5,scale=w='min(1280,iw)':h=-2", command)
            self.assertIn("fps=5,scale=w='min(640,iw)':h=-2", command)
            with self.assertRaises(ValueError):
                FfmpegMediaIngest("local.mp4", ring, width=1921)

    def test_real_ffmpeg_retains_high_resolution_without_upscaling_sd(self):
        ffmpeg = bundled_binary("ffmpeg")
        if not ffmpeg:
            self.skipTest("本地 FFmpeg 不可用。")
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for width, height, shared_width, expected in ((1920, 1080, 1280, (720, 1280)), (1920, 1080, 1920, (1080, 1920)), (640, 360, 1920, (360, 640))):
                with self.subTest(width=width):
                    source = root / f"synthetic-{width}.mp4"
                    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                    f"testsrc2=size={width}x{height}:rate=5", "-t", "1",
                                    "-c:v", "libx264", "-preset", "ultrafast", str(source)],
                                   capture_output=True, timeout=20, check=True, shell=False)
                    ring = DiskSegmentRingBuffer(root / f"ring-{width}", retention_seconds=60)
                    ingest = FfmpegMediaIngest(str(source), ring, width=shared_width, ffmpeg_path=ffmpeg)
                    try:
                        frame = next(ingest.iter_frames(max_frames=1))
                        image = cv2.imdecode(np.frombuffer(frame.jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                        self.assertEqual(image.shape[:2], expected)
                    finally:
                        ingest.close()


if __name__ == "__main__":
    unittest.main()
