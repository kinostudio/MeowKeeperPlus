"""GPU 選擇與實際 ONNX cat-only／高解析度／追蹤契約。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from meowkeeper.core.detection.frame_analysis import YoloFrameAnalyzer
from meowkeeper.core.inference.accelerated_yolo import (
    AcceleratedYolo,
    AcceleratorInitializationError,
    select_accelerated,
    verified_export,
)
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.core.tracking.botsort_tracker import BoTSORTTracker
from meowkeeper.runtime import default_model_file


class AcceleratedYoloTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = default_model_file()
        cls.root = cls.model.parent.parent
        cls.analyzer = YoloFrameAnalyzer(str(cls.model), device="onnx:cpu", input_size=1920)

    def test_verified_export_rejects_missing_or_changed_source(self):
        self.assertIsNotNone(verified_export(self.model))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yolo11n.pt"
            path.write_bytes(b"different-model")
            self.assertIsNone(verified_export(path))

    def test_real_1080p_cat_and_negative(self):
        import cv2
        for name, expected in (("cat.jpg", True), ("bus.jpg", False)):
            image = cv2.imread(str(self.root / "tests/assets" / name))
            image = cv2.resize(image, (1920, 1080))
            ok, encoded = cv2.imencode(".jpg", image)
            self.assertTrue(ok)
            result = self.analyzer.analyze(FramePacket(1, datetime.now(timezone.utc), "test", encoded.tobytes()))
            self.assertEqual(result.triggered, expected)
            self.assertEqual(result.details["tensor_shape"], (1, 3, 1088, 1920))
            self.assertEqual(result.details["inference_size"], 1920)

    def test_real_exported_tracking_keeps_id(self):
        tracker = BoTSORTTracker(str(self.model), device="onnx:cpu", input_size=640)
        frame = FramePacket(1, datetime.now(timezone.utc), "test", (self.root / "tests/assets/cat.jpg").read_bytes())
        first, second = tracker.update(frame), tracker.update(frame)
        self.assertTrue(first)
        self.assertEqual(first[0].track_id, second[0].track_id)
        tracker.reset()
        self.assertTrue(tracker.update(frame))

    def test_real_tracking_reports_nested_gmc_and_tracker_stages(self):
        tracker = BoTSORTTracker(str(self.model), device="onnx:cpu", input_size=640)
        frame = FramePacket(1, datetime.now(timezone.utc), "test", (self.root / "tests/assets/cat.jpg").read_bytes())
        self.assertTrue(tracker.update(frame))
        timings = tracker._model.last_timings
        for name in ("tracking_ms", "tracker_input_transfer_ms", "tracker_engine_update_ms",
                     "tracker_output_format_ms", "tracker_gmc_apply_ms"):
            self.assertIn(name, timings)
            if timings[name] is not None:
                self.assertGreaterEqual(timings[name], 0)
        if tracker._model._tracker_gmc_timing_available:
            self.assertIsNotNone(timings["tracker_gmc_apply_ms"])

    def test_gpu_fault_retries_once_cpu_and_retains_cat(self):
        model = AcceleratedYolo(verified_export(self.model), "CPU")
        model.backend = "ONNX Runtime CUDA"
        original = model._run
        calls = 0
        def run(tensor):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("CUDA failure")
            return original(tensor)
        with patch.object(model, "_run", side_effect=run):
            import cv2
            result = model.predict(source=cv2.imread(str(self.root / "tests/assets/cat.jpg")), imgsz=640, conf=0.45)
        self.assertEqual(calls, 2)
        self.assertEqual(model.backend, "CPU")
        self.assertEqual(model.backend_diagnostics["stage"], "runtime_inference")
        self.assertEqual(model.backend_diagnostics["error_type"], "RuntimeError")
        self.assertGreater(len(result[0].boxes), 0)

    def test_unavailable_gpu_falls_back_without_auto_install(self):
        with patch("meowkeeper.core.inference.accelerated_yolo.AcceleratedYolo",
                   side_effect=AcceleratorInitializationError("gpu_warmup_640", "RuntimeError")):
            diagnostics = {}
            self.assertIsNone(select_accelerated(self.model, "intel:gpu", diagnostics=diagnostics))
            self.assertIsNone(select_accelerated(self.model, "onnx:cuda"))
        self.assertEqual(diagnostics["status"], "fallback")
        self.assertEqual(diagnostics["stage"], "gpu_warmup_640")
        self.assertEqual(diagnostics["error_type"], "RuntimeError")
        self.assertIsNone(select_accelerated(self.model, "cpu"))
