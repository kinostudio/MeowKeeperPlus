from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.domain import (
    COCO_CAT_CLASS_ID,
    DEFAULT_VISION_ANALYSIS,
    MX250_MINIMUM,
    MediaBufferSettings,
    PerformanceProfile,
    VisionAnalysisSettings,
    get_performance_profile,
)


class MediaBufferSettingsTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        MediaBufferSettings().validate()

    def test_pre_record_must_not_exceed_ring_buffer(self) -> None:
        with self.assertRaisesRegex(ValueError, "不可大於"):
            MediaBufferSettings(ring_buffer_seconds=10, live_pre_seconds=11).validate()

    def test_configurable_ranges_are_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "10 到 120"):
            MediaBufferSettings(ring_buffer_seconds=9).validate()
        with self.assertRaisesRegex(ValueError, "0 到 30"):
            MediaBufferSettings(live_post_seconds=31).validate()


class PerformanceProfileTests(unittest.TestCase):
    def test_mx250_minimum_profile_is_conservative(self) -> None:
        profile = get_performance_profile("mx250_minimum")
        profile.validate()
        self.assertEqual(profile.minimum_ram_gib, 8.0)
        self.assertEqual(profile.recommended_ram_gib, 16.0)
        self.assertEqual(profile.minimum_vram_mib, 2048)
        self.assertEqual(profile.max_visual_models, 1)
        self.assertEqual(profile.model_batch_size, 1)
        self.assertLessEqual(profile.inference_input_size, 640)
        self.assertLessEqual(profile.analysis_fps, 5)
        self.assertEqual(profile.identity_sampling_fps, 1.0)
        self.assertEqual(profile.behavior_sampling_hz, 1.0)
        self.assertTrue(profile.conditional_identity)

    def test_more_system_ram_does_not_change_gpu_defaults(self) -> None:
        self.assertEqual(MX250_MINIMUM.max_visual_models, 1)
        self.assertEqual(MX250_MINIMUM.model_batch_size, 1)
        self.assertEqual(MX250_MINIMUM.analysis_fps, 5)

    def test_mx250_profile_rejects_multiple_models_or_high_fps(self) -> None:
        values = dict(MX250_MINIMUM.__dict__)
        values["max_visual_models"] = 2
        with self.assertRaisesRegex(ValueError, "一個視覺模型"):
            PerformanceProfile(**values).validate()

        values = dict(MX250_MINIMUM.__dict__)
        values["analysis_fps"] = 10
        with self.assertRaisesRegex(ValueError, "5 FPS"):
            PerformanceProfile(**values).validate()

    def test_unknown_profile_does_not_silently_fallback(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知效能 profile"):
            get_performance_profile("unknown")

    def test_config_example_references_a_known_profile(self) -> None:
        payload = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        profile_name = payload["performance"]["profile"]
        self.assertEqual(get_performance_profile(profile_name), MX250_MINIMUM)


class VisionAnalysisSettingsTests(unittest.TestCase):
    def test_default_cat_detector_contract(self) -> None:
        DEFAULT_VISION_ANALYSIS.validate()
        self.assertEqual(DEFAULT_VISION_ANALYSIS.cat_class_id, COCO_CAT_CLASS_ID)
        self.assertEqual(DEFAULT_VISION_ANALYSIS.confidence_threshold, 0.45)
        self.assertEqual(DEFAULT_VISION_ANALYSIS.input_size, 640)

    def test_high_resolution_modes_are_supported_but_not_the_mx250_default(self) -> None:
        for size in (640, 960, 1280, 1920):
            with self.subTest(size=size):
                VisionAnalysisSettings(input_size=size).validate()
        with self.assertRaisesRegex(ValueError, "只支援"):
            VisionAnalysisSettings(input_size=1080).validate()

    def test_other_coco_class_is_rejected(self) -> None:
        values = dict(DEFAULT_VISION_ANALYSIS.__dict__)
        values["cat_class_id"] = 17
        with self.assertRaisesRegex(ValueError, "class id 15"):
            VisionAnalysisSettings(**values).validate()


if __name__ == "__main__":
    unittest.main()
