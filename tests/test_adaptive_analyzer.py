import unittest
from unittest.mock import patch

from meowkeeper.core.detection.frame_analysis import FrameAnalysis
from meowkeeper.core.inference.adaptive_analyzer import AdaptiveAnalyzer
from meowkeeper.core.inference.gpu_telemetry import parse_gpu_details, parse_gpu_sample


class FakeAnalyzer:
    inference_backend = "CPU"
    calls = 0

    def analyze(self, _frame):
        self.calls += 1
        return FrameAnalysis(True, "cat_detected", 0.8, "測試貓咪", {"cat_count": 1})


class AdaptiveAnalyzerTests(unittest.TestCase):
    def test_explicit_retry_restores_preferred_cap_not_permanent_one_fps(self):
        wrapper = AdaptiveAnalyzer(FakeAnalyzer(), fps=5, logger=lambda _: None)
        wrapper.policy.fps = 2
        wrapper.policy.state = "degraded"
        self.assertTrue(wrapper.request_retry())
        with patch.object(wrapper, "_sample", return_value=(15, 53, False)):
            wrapper.analyze(None)
        self.assertEqual(wrapper.policy.fps, 5)
        self.assertEqual(wrapper.policy.size, 640)

    def test_selected_size_propagates_through_v02_session_tracker_proxy(self):
        worker = type("Worker", (), {"input_size": 1280})()
        session = type("Session", (), {"tracker": worker})()
        host_analyzer = type("V02", (), {"tracker": session})()
        wrapper = AdaptiveAnalyzer(host_analyzer, fps=5, logger=lambda _: None, input_size=1280)
        wrapper._set_input_size(960)
        self.assertEqual(worker.input_size, 960)
        self.assertEqual(wrapper.snapshot()["effective_input_size"], 960)

    def test_stop_can_cancel_only_the_isolated_worker(self):
        class Worker:
            def __init__(self):
                self.cancelled = False

            def snapshot(self):
                return {"pid": 1234}

            def cancel_pending(self):
                self.cancelled = True

        worker = Worker()
        wrapper = AdaptiveAnalyzer(worker, fps=5, logger=lambda _: None)
        wrapper.cancel_pending()
        self.assertTrue(worker.cancelled)

    def test_gpu_identity_and_unavailable_fields(self):
        csv = "GPU-test, NVIDIA Test, 42, 81, 900\n"
        self.assertEqual(parse_gpu_sample(csv, uuid="GPU-test", name=None), (42, 81, 900))
        self.assertEqual(parse_gpu_sample(csv, uuid="wrong", name="NVIDIA Test"), (None, None, None))
        self.assertEqual(parse_gpu_sample(csv * 2, uuid=None, name="NVIDIA Test"), (None, None, None))
        self.assertEqual(parse_gpu_sample(csv.replace("42", "N/A"), uuid="GPU-test", name=None),
                         (None, None, None))

    def test_gpu_details_distinguish_device_metrics_and_unsupported_fields(self):
        row = ("GPU-test, NVIDIA Test, 42, 18, 55, 1024, 2048, 1024, 900, 500, "
               "8.5, [N/A], Not Active, Not Active, Active, Not Active, Not Active, Active\n")
        value = parse_gpu_details(row, uuid="GPU-test", name=None)
        self.assertEqual(value["gpu_model"], "NVIDIA Test")
        self.assertEqual(value["gpu_kernel_busy_percent"], 42)
        self.assertEqual(value["memory_controller_busy_percent"], 18)
        self.assertEqual(value["dedicated_memory_used_mib"], 1024)
        self.assertIsNone(value["power_limit_w"])
        self.assertEqual(value["throttle_active_reasons"], ["硬體過熱降頻", "GPU 閒置降頻"])
        self.assertIsNone(parse_gpu_details(row, uuid="GPU-other", name=None)["gpu_model"])

    def test_skipped_frame_does_not_claim_no_cat(self):
        analyzer = FakeAnalyzer()
        wrapper = AdaptiveAnalyzer(analyzer, fps=5, logger=lambda _: None)
        with patch.object(wrapper, "_sample", return_value=(0, None, False)), \
                patch("meowkeeper.core.inference.adaptive_analyzer.time.monotonic", side_effect=[0, .01, .02, .05]):
            self.assertTrue(wrapper.analyze(None).triggered)
            skipped = wrapper.analyze(None)
        self.assertFalse(skipped.triggered)
        self.assertNotIn("cat_count", skipped.details)
        self.assertEqual(analyzer.calls, 1)

    def test_cpu_failure_suspends_without_stopping_media(self):
        wrapper = AdaptiveAnalyzer(FakeAnalyzer(), fps=5, logger=lambda _: None)
        with patch.object(wrapper, "_sample", return_value=(0, None, False)), \
                patch.object(wrapper.analyzer, "analyze", side_effect=RuntimeError("測試錯誤")):
            wrapper._last_started = None
            self.assertFalse(wrapper.analyze(None).triggered)
            wrapper._last_started = None
            self.assertFalse(wrapper.analyze(None).triggered)
        self.assertEqual(wrapper.policy.state, "suspended")
        self.assertEqual(wrapper.snapshot()["failure_count"], 2)

    def test_recovery_does_not_pause_working_degraded_ai(self):
        wrapper = AdaptiveAnalyzer(FakeAnalyzer(), fps=5, logger=lambda _: None)
        wrapper.policy.state = "degraded"
        wrapper.policy.fps = 2
        wrapper.policy.observe(0, backend="CPU", load_percent=10)
        wrapper.policy.observe(301, backend="CPU", load_percent=10)
        self.assertEqual(wrapper.policy.state, "degraded")
        self.assertTrue(wrapper.policy.recovery_available)

    def test_suspended_ai_can_recover_despite_old_slow_latency(self):
        wrapper = AdaptiveAnalyzer(FakeAnalyzer(), fps=5, logger=lambda _: None)
        wrapper.policy.state = "suspended"
        wrapper.policy.observe(0, backend="CPU", load_percent=10, too_slow=True)
        wrapper.policy.observe(301, backend="CPU", load_percent=10, too_slow=True)
        self.assertEqual(wrapper.policy.state, "recovery_available")

    def test_measured_fps_counts_completed_inferences_only(self):
        wrapper = AdaptiveAnalyzer(FakeAnalyzer(), fps=5, logger=lambda _: None)
        wrapper._completed.extend([1, 3, 4, 5])
        with patch("meowkeeper.core.inference.adaptive_analyzer.time.monotonic", return_value=8):
            self.assertEqual(wrapper.measured_fps(), 0.6)
        with patch("meowkeeper.core.inference.adaptive_analyzer.time.monotonic", return_value=20):
            self.assertEqual(wrapper.measured_fps(), 0)
