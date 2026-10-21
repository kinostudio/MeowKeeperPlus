import threading
import unittest

from meowkeeper.core.inference.adaptive_policy import AdaptivePolicy, AdaptiveSettings
from meowkeeper.core.media.latest_frame import LatestFrameReader


class AdaptivePolicyTests(unittest.TestCase):
    def test_short_spike_and_sustained_drop(self):
        policy = AdaptivePolicy(AdaptiveSettings(preferred_size=1280))
        policy.observe(0, backend="CPU", load_percent=50)
        self.assertFalse(policy.observe(59, backend="CPU", load_percent=50))
        self.assertTrue(policy.observe(60, backend="CPU", load_percent=50))
        self.assertEqual(policy.size, 960)
        self.assertEqual(policy.settings.preferred_size, 1280)
        policy.observe(61, backend="CPU", load_percent=50)
        self.assertEqual(policy.size, 960)

    def test_temperature_and_critical(self):
        policy = AdaptivePolicy(AdaptiveSettings())
        policy.observe(0, backend="CPU", load_percent=10, temperature_c=86)
        policy.observe(30, backend="CPU", load_percent=10, temperature_c=86)
        self.assertEqual(policy.fps, 3)
        policy.observe(32, backend="CPU", temperature_c=96)
        self.assertNotEqual(policy.state, "suspended")
        policy.observe(34, backend="CPU", temperature_c=96)
        self.assertEqual(policy.state, "suspended")

    def test_missing_and_recovery_hysteresis(self):
        policy = AdaptivePolicy(AdaptiveSettings())
        policy.observe(0, backend="CPU", memory_pressure=True)
        policy.observe(400, backend="CPU")
        self.assertEqual(policy.state, "suspended")
        policy.observe(401, backend="CPU", load_percent=10, temperature_c=70)
        policy.observe(702, backend="CPU", load_percent=10, temperature_c=70)
        self.assertEqual(policy.state, "recovery_available")
        self.assertEqual(policy.size, 640)

    def test_lowest_fps_suspends(self):
        policy = AdaptivePolicy(AdaptiveSettings(preferred_fps=1))
        policy.observe(0, backend="CPU", too_slow=True)
        policy.observe(61, backend="CPU", too_slow=True)
        self.assertEqual(policy.state, "suspended")

    def test_invalid_thresholds(self):
        for kwargs in ({"target_percent": float("nan")}, {"cpu_warning_c": 100, "cpu_critical_c": 95},
                       {"preferred_size": 1080}, {"preferred_fps": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AdaptiveSettings(**kwargs)


class LatestFrameTests(unittest.TestCase):
    def test_slow_ai_does_not_block_reader(self):
        release = threading.Event()
        drained = threading.Event()
        seen = []

        def source():
            yield 0
            release.wait(2)
            yield from range(1, 100)
            drained.set()

        reader = LatestFrameReader(source(), on_frame=seen.append, close_source=lambda: None)
        frames = iter(reader)
        self.assertEqual(next(frames), 0)
        release.set()
        self.assertTrue(drained.wait(2))
        self.assertEqual(next(frames), 99)
        self.assertEqual(seen, list(range(100)))
        self.assertGreater(reader.dropped, 0)
        timing = reader.snapshot()["timings"]
        self.assertIn("media_frame_interarrival_ms", timing)
        self.assertIn("latest_frame_callback_ms", timing)
        reader.close()

    def test_reader_exception_is_propagated(self):
        def source():
            raise ValueError("測試來源失敗")
            yield 0
        reader = LatestFrameReader(source(), on_frame=lambda _: None, close_source=lambda: None)
        with self.assertRaises(ValueError):
            list(reader)
        reader.close()
