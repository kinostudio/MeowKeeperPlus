from __future__ import annotations

import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from tools import trial_xiaomi_miss_ptz_026c02 as trial


class PtzTrialFlowTests(unittest.TestCase):
    def test_vertical_move_always_sends_stop_without_claiming_motor_ack(self) -> None:
        calls = []
        buffer = io.BytesIO()
        Image.new("RGB", (32, 24), "gray").save(buffer, format="JPEG")
        def response(url, **kwargs):
            calls.append(kwargs["body"])
            return {"connected": True}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(trial, "ROOT", Path(directory)), \
                patch.object(trial, "_capture_frame", return_value=(buffer.getvalue(), 1)), \
                patch.object(trial, "_json", side_effect=response), \
                patch.object(trial.time, "sleep"):
            result = trial._vertical_trial("http://127.0.0.1/ptz", Path(directory) / "latest.jpg", "up")
        self.assertEqual(calls, [{"action": "move", "direction": "up"}, {"action": "stop"}])
        self.assertTrue(result["stop_command_sent"])
        self.assertFalse(result["motor_acknowledged"])

    def _exercise(self, allow: bool, *, position_only: bool = False) -> tuple[dict, list[str]]:
        calls: list[str] = []
        state = {"connected": True, "can_left": True, "can_right": True, "position": None}

        def response(url: str, **kwargs):
            if url.endswith("/api/streams"):
                return {trial.TRIAL_STREAM: {"producers": [{}], "consumers": [{}]}}
            if "refresh=true" in url:
                raise urllib.error.HTTPError(url, 504, "timeout", {}, io.BytesIO(b"response timeout"))
            if kwargs.get("method") == "POST":
                calls.append(kwargs["body"]["action"])
            return state

        process = Mock()
        process.poll.return_value = None
        report = {}
        frame = io.BytesIO()
        Image.new("RGB", (32, 24), "gray").save(frame, format="JPEG")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(trial, "ROOT", Path(directory)), \
                patch.object(trial, "_capture_frame", return_value=(frame.getvalue(), 1)), \
                patch.object(trial.subprocess, "Popen", return_value=process), \
                patch.object(trial, "_wait_url", return_value=True), \
                patch.object(trial, "_json", side_effect=response), \
                patch.object(trial.time, "sleep"):
            if allow or position_only:
                trial._trial(Path(directory) / "config.yaml", report,
                             allow_relative_without_position=allow, position_only=position_only)
            else:
                with self.assertRaises(urllib.error.HTTPError):
                    trial._trial(Path(directory) / "config.yaml", report)
        return report, calls

    def test_position_only_timeout_never_sends_move_or_stop(self) -> None:
        report, calls = self._exercise(False, position_only=True)
        self.assertEqual(calls, [])
        self.assertFalse(report["position_readback_verified"])
        self.assertEqual(report["stage"], "position_readback_failed")

    def test_default_position_timeout_does_not_move_camera(self) -> None:
        report, calls = self._exercise(False)
        self.assertEqual(calls, [])
        self.assertEqual(report["ptz_error_category"], "response_timeout")

    def test_explicit_relative_trial_always_stops_and_never_claims_visual_success(self) -> None:
        report, calls = self._exercise(True)
        self.assertEqual(calls, ["move", "stop"])
        self.assertTrue(report["stop_command_sent"])
        self.assertFalse(report["stop_acknowledged"])
        self.assertTrue(report["move_command_sent"])
        self.assertFalse(report["move_acknowledged"])
        self.assertFalse(report["position_readback_verified"])
        self.assertFalse(report["physical_movement_verified"])
        self.assertEqual(report["stage"], "relative_commands_sent_visual_verification_required")
        self.assertTrue(report["frame_evidence"]["visual_review_required"])
        self.assertEqual(report["frame_evidence"]["mean_pixel_difference"], 0)

    def test_changed_jpeg_is_evidence_not_automatic_camera_movement_proof(self) -> None:
        frames = []
        for color in ("black", "white"):
            buffer = io.BytesIO()
            Image.new("RGB", (32, 24), color).save(buffer, format="JPEG")
            frames.append(buffer.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            result = trial._frame_evidence(*frames, Path(directory) / "private")
            self.assertGreater(result["mean_pixel_difference"], 250)
            self.assertFalse(result["physical_movement_verified"])
            self.assertTrue(result["visual_review_required"])
            self.assertEqual(len(list((Path(directory) / "private").glob("*.jpg"))), 2)

    def test_capture_rejects_stale_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.jpg"
            buffer = io.BytesIO()
            Image.new("RGB", (32, 24), "gray").save(buffer, format="JPEG")
            path.write_bytes(buffer.getvalue())
            stamp = path.stat().st_mtime_ns
            self.assertEqual(trial._capture_frame(path)[1], stamp)
            with patch.object(trial.time, "monotonic", side_effect=[0, 1, 5]), \
                    patch.object(trial.time, "sleep"), \
                    self.assertRaisesRegex(RuntimeError, "trial_frame_unavailable"):
                trial._capture_frame(path, newer_than=stamp)
