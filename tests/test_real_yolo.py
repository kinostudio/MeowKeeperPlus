from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.core.detection.frame_analysis import YoloFrameAnalyzer
from meowkeeper.core.events.analysis_pipeline import AnalysisPipeline
from meowkeeper.core.inference.process_worker import IsolatedInferenceWorker
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.domain import COCO_CAT_CLASS_ID, DEFAULT_VISION_ANALYSIS
from meowkeeper.runtime import default_model_file

ASSETS = ROOT / "tests" / "assets"


def packet(name: str) -> FramePacket:
    path = ASSETS / name
    return FramePacket(
        sequence=1,
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_url=str(path),
        jpeg_bytes=path.read_bytes(),
    )


class RealYoloInferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not default_model_file().is_file():
            raise AssertionError("預設模型不存在")
        if not (ASSETS / "cat.jpg").is_file():
            raise AssertionError("測試貓咪影像不存在")
        if not (ASSETS / "bus.jpg").is_file():
            raise AssertionError("測試非貓影像不存在")
        cls.analyzer = YoloFrameAnalyzer(str(default_model_file()), device="cpu")

    def test_defaults_target_only_coco_cat(self) -> None:
        self.assertEqual(self.analyzer.cat_class_id, COCO_CAT_CLASS_ID)
        self.assertEqual(self.analyzer.confidence_threshold, DEFAULT_VISION_ANALYSIS.confidence_threshold)
        self.assertIn(self.analyzer.inference_backend, {"PyTorch CUDA", "CPU"})

    def test_real_cat_image_finds_cat(self) -> None:
        result = self.analyzer.analyze(packet("cat.jpg"))
        self.assertTrue(result.triggered)
        self.assertEqual(result.event_type, "cat_detected")
        self.assertGreaterEqual(result.details["cat_count"], 1)
        self.assertTrue(
            all(item["class_id"] == COCO_CAT_CLASS_ID for item in result.details["detections"])
        )

    def test_isolated_worker_runs_real_cat_model_at_selected_high_resolution(self) -> None:
        image = self.analyzer._cv2.imdecode(
            self.analyzer._np.frombuffer(packet("cat.jpg").jpeg_bytes, dtype=self.analyzer._np.uint8),
            self.analyzer._cv2.IMREAD_COLOR,
        )
        height, width = image.shape[:2]
        scale = 1920 / max(width, height)
        resized = self.analyzer._cv2.resize(image, (round(width * scale), round(height * scale)))
        ok, encoded = self.analyzer._cv2.imencode(".jpg", resized)
        self.assertTrue(ok)
        frame = FramePacket(5, datetime.now(timezone.utc), "isolated-test", encoded.tobytes())
        worker = IsolatedInferenceWorker(
            "detect",
            {"model_path": str(default_model_file()), "device": "cpu",
             "confidence_threshold": 0.45, "input_size": 640},
            startup_timeout=120, inference_timeout=20,
        )
        try:
            for size in (640, 960, 1280, 1920):
                with self.subTest(input_size=size):
                    worker.input_size = size
                    result = worker.analyze(frame)
                    self.assertTrue(result.triggered, result.message_zh_tw)
                    self.assertEqual(result.details["inference_size"], size)
            self.assertTrue(worker.alive)
            self.assertNotEqual(worker.process.pid, os.getpid())
            self.assertEqual(worker.inference_backend, "CPU")
        finally:
            worker.close()

    def test_injected_cuda_failure_recovers_with_real_cpu_inference(self) -> None:
        analyzer = YoloFrameAnalyzer(str(default_model_file()), device="cpu")
        predict = analyzer._model.predict
        analyzer.device = "cuda:0"
        analyzer.inference_backend = "PyTorch CUDA"
        calls = []

        def first_failure(**kwargs):
            calls.append(kwargs["device"])
            if len(calls) == 1:
                raise RuntimeError("CUDA out of memory")
            return predict(**kwargs)

        with patch.object(analyzer._model, "predict", side_effect=first_failure):
            result = analyzer.analyze(packet("cat.jpg"))
        self.assertTrue(result.triggered)
        self.assertEqual(calls, ["cuda:0", "cpu"])
        self.assertEqual(analyzer.inference_backend, "CPU")

    def test_non_cat_image_does_not_trigger_cat_event(self) -> None:
        result = self.analyzer.analyze(packet("bus.jpg"))
        self.assertFalse(result.triggered)
        self.assertIsNone(result.event_type)

        notifier = Mock()
        pipeline = AnalysisPipeline(self.analyzer, notifier=notifier, chat_ids=["not-used"])
        self.assertIsNone(pipeline.process(packet("bus.jpg")))
        notifier.send_photo.assert_not_called()

    def test_telegram_disabled_does_not_send(self) -> None:
        pipeline = AnalysisPipeline(self.analyzer, notifier=None, chat_ids=["not-used"])
        event = pipeline.process(packet("cat.jpg"))
        self.assertIsNotNone(event)
        self.assertEqual(event.sent, ())


if __name__ == "__main__":
    unittest.main()
