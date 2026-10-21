from __future__ import annotations

import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from meowkeeper.core.media.sources import FramePacket
from meowkeeper.core.tracking import BoTSORTTracker
from meowkeeper.core.tracking.botsort_tracker import TrackingConfigurationError


class _FakeBoxes:
    def __init__(self) -> None:
        self.id = np.asarray([7])
        self.cls = np.asarray([15])
        self.conf = np.asarray([0.91])
        self.xyxy = np.asarray([[10, 10, 100, 100]], dtype=np.float32)


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.predictor = None

    def track(self, **kwargs: object):
        self.calls.append(kwargs)
        return [types.SimpleNamespace(boxes=_FakeBoxes())]


class TrackingContractTests(unittest.TestCase):
    def test_compatibility_retry_keeps_cpu_after_cuda_fallback(self) -> None:
        class FaultModel(_FakeModel):
            def to(self, device):
                self.actual_device = device

            def track(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise RuntimeError("CUDA out of memory")
                if "with_reid" in kwargs:
                    raise SyntaxError("with_reid is handled by tracker YAML")
                self.actual_device = kwargs["device"]
                return [types.SimpleNamespace(boxes=_FakeBoxes())]

        fake = FaultModel()
        tracker = BoTSORTTracker(model=fake, device="cpu")
        tracker.device = "cuda:0"
        tracker.inference_backend = "PyTorch CUDA"
        frame = FramePacket(
            1, datetime.now(timezone.utc), "public-test-fixture",
            (Path(__file__).parent / "assets" / "cat.jpg").read_bytes(),
        )
        self.assertEqual(len(tracker.update(frame)), 1)
        self.assertEqual([call["device"] for call in fake.calls], ["cuda:0", "cpu", "cpu"])
        self.assertEqual(fake.actual_device, "cpu")
        self.assertEqual(tracker.inference_backend, "CPU")

    def test_botsort_uses_persist_reid_and_cat_only(self) -> None:
        image = cv2.imread(str(Path(__file__).parent / "assets" / "cat.jpg"))
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        fake = _FakeModel()
        tracker = BoTSORTTracker(model=fake)
        frame = FramePacket(1, datetime.now(timezone.utc), "video-file", encoded.tobytes())
        observations = tracker.update(frame)
        self.assertEqual(len(observations), 1)
        self.assertTrue(fake.calls[0]["persist"])
        self.assertTrue(fake.calls[0]["with_reid"])
        self.assertEqual(fake.calls[0]["classes"], [15])
        self.assertEqual(fake.calls[0]["imgsz"], 640)
        tracker.reset("source_changed")
        self.assertEqual(tracker.reset_reason, "source_changed")

    def test_high_resolution_crop_uses_original_frame_coordinates(self) -> None:
        class HighResolutionModel(_FakeModel):
            def track(self, **kwargs):
                self.calls.append(kwargs)
                boxes = _FakeBoxes()
                boxes.xyxy = np.asarray([[700, 200, 940, 440]], dtype=np.float32)
                return [types.SimpleNamespace(boxes=boxes)]

        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[200:440, 700:940] = (0, 200, 0)
        _, jpeg = cv2.imencode(".jpg", image)
        model = HighResolutionModel()
        tracker = BoTSORTTracker(model=model, device="cpu", input_size=1280)
        result = tracker.update(FramePacket(1, datetime.now(timezone.utc), "local-test", jpeg.tobytes()))[0]
        self.assertEqual(model.calls[0]["source"].shape[:2], (720, 1280))
        self.assertEqual(model.calls[0]["imgsz"], 1280)
        self.assertEqual((result.frame_width, result.frame_height), (1280, 720))
        crop = cv2.imdecode(np.frombuffer(result.crop_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(crop.shape[:2], (240, 240))
        self.assertGreater(float(crop[:, :, 1].mean()), 190)

    def test_tracker_accepts_only_supported_high_resolution_sizes(self) -> None:
        for size in (640, 960, 1280, 1920):
            with self.subTest(size=size):
                tracker = BoTSORTTracker(model=_FakeModel(), input_size=size)
                self.assertEqual(tracker.input_size, size)
        with self.assertRaises(TrackingConfigurationError):
            BoTSORTTracker(model=_FakeModel(), input_size=1080)


if __name__ == "__main__":
    unittest.main()
