from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from meowkeeper.core.detection.frame_analysis import FrameAnalysis, YoloFrameAnalyzer
from meowkeeper.core.inference.adaptive_analyzer import AdaptiveAnalyzer
from meowkeeper.core.inference.gpu_telemetry import CachedGpuSampler
from meowkeeper.core.inference.priority import InferencePriority
from meowkeeper.core.inference.process_worker import IsolatedInferenceWorker
from meowkeeper.core.inference.resource_monitor import InferenceLoadController
from meowkeeper.core.inference.scheduler import GPUInferenceScheduler
from meowkeeper.core.inference.timings import StageTimings
from meowkeeper.core.media.sources import FramePacket


def timed_worker_fixture(connection, role, config):
    connection.send(("ready", "ONNX Runtime CUDA", "cuda:0"))
    try:
        while connection.recv()[0] != "close":
            connection.send(("ok", [], "ONNX Runtime CUDA", "cuda:0", {
                "stages": {"worker_action_ms": 2.0, "jpeg_decode_ms": .5},
                "provider_node_counts": {"CUDAExecutionProvider": 308},
            }))
    except (EOFError, OSError):
        pass
    finally:
        connection.close()


class InferenceDiagnosticsTests(unittest.TestCase):
    def test_basic_yolo_analyzer_exposes_decode_model_and_result_stage_timings(self):
        image = SimpleNamespace(shape=(100, 200, 3))
        result = SimpleNamespace(
            names={15: "cat"},
            boxes=SimpleNamespace(cls=[15], conf=[0.9], xyxy=[[10, 20, 80, 90]]),
            speed={"preprocess": 1.0, "inference": 2.0, "postprocess": 3.0},
        )
        model = SimpleNamespace(
            predict=lambda **_kwargs: [result], last_timings={"model_internal_stage_ms": 2.0},
        )
        analyzer = YoloFrameAnalyzer.__new__(YoloFrameAnalyzer)
        analyzer._np = SimpleNamespace(uint8=0, frombuffer=lambda *_args, **_kwargs: b"jpeg")
        analyzer._cv2 = SimpleNamespace(IMREAD_COLOR=1, imdecode=lambda *_args: image)
        analyzer._model = model
        analyzer.device = "cpu"
        analyzer.inference_backend = "CPU"
        analyzer.backend_warning = None
        analyzer.confidence_threshold = 0.25
        analyzer.input_size = 640
        analyzer.cat_class_id = 15

        output = analyzer.analyze(FramePacket(1, datetime.now(timezone.utc), "fixture", b"jpeg"))

        self.assertTrue(output.triggered)
        self.assertGreaterEqual(analyzer.last_timings["jpeg_decode_ms"], 0)
        self.assertGreaterEqual(analyzer.last_timings["model_call_wall_ms"], 0)
        self.assertGreaterEqual(analyzer.last_timings["detection_result_adaptation_ms"], 0)
        self.assertGreaterEqual(analyzer.last_timings["analysis_total_ms"], 0)
        self.assertIsNone(analyzer.last_timings["cpu_fallback_ms"])
        self.assertEqual(analyzer.last_timings["model_internal_stage_ms"], 2.0)
        self.assertEqual(analyzer.last_timings["ultralytics_inference_reported_ms"], 2.0)

    def test_queue_wait_is_measured_separately_from_high_task_execution(self):
        entered, release = threading.Event(), threading.Event()
        scheduler = GPUInferenceScheduler(max_pending_tasks=2)

        def low_task():
            entered.set()
            release.wait(2)

        try:
            low = scheduler.submit(low_task, priority=InferencePriority.LOW)
            self.assertTrue(entered.wait(1))
            high = scheduler.submit(lambda: "cat", priority=InferencePriority.HIGH)
            self.assertFalse(high.done())  # Running low tasks are not preemptible.
            release.set()
            self.assertEqual(high.result(timeout=1), "cat")
            low.result(timeout=1)
            self.assertEqual(scheduler.timings.snapshot()["high_queue_wait_ms"]["samples"], 1)
        finally:
            release.set()
            scheduler.shutdown(wait=True)

    def test_lower_safety_cap_still_wins_over_higher_activity_cap(self):
        inner = InferenceLoadController(normal_fps=5, active_fps=5, quiet_fps=1)

        class Analyzer:
            inference_backend = "CPU"
            pipeline = SimpleNamespace(load_controller=inner)

            def analyze(self, frame):
                return FrameAnalysis(False, None, None, "測試", {
                    "analysis_skipped": not inner.should_analyze(frame.captured_at)})

        clock = [0.0]
        wrapper = AdaptiveAnalyzer(Analyzer(), fps=1, logger=lambda _: None)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("meowkeeper.core.inference.adaptive_analyzer.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(wrapper, "_sample", return_value=(0, None, False)):
            for index in range(51):
                clock[0] = index * .2
                wrapper.analyze(SimpleNamespace(captured_at=start + timedelta(seconds=clock[0])))
            clock[0] = 10.1
            self.assertLessEqual(wrapper.measured_fps(), 1.0)
            self.assertEqual(wrapper.snapshot()["combined_sampling_fps_cap"], 1)
            self.assertEqual(wrapper.policy.fps, 1)
        wrapper.close()

    def test_activity_and_adaptive_sampling_do_not_compound_into_point_eight_fps(self):
        inner = InferenceLoadController(normal_fps=3, active_fps=5, quiet_fps=1)
        inner.last_seen_cat_monotonic = -100

        class Analyzer:
            inference_backend = "CPU"
            pipeline = SimpleNamespace(load_controller=inner)

            def analyze(self, frame):
                return FrameAnalysis(False, None, None, "測試", {
                    "analysis_skipped": not inner.should_analyze(frame.captured_at)})

        clock = [0.0]
        wrapper = AdaptiveAnalyzer(Analyzer(), fps=3, logger=lambda _: None)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("meowkeeper.core.inference.adaptive_analyzer.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(wrapper, "_sample", return_value=(0, None, False)):
            for index in range(51):
                clock[0] = index * .2
                wrapper.analyze(SimpleNamespace(captured_at=start + timedelta(seconds=clock[0])))
            clock[0] = 10.1
            self.assertEqual(wrapper.measured_fps(), 1.0)
        wrapper.close()

    def test_worker_roundtrip_preserves_stage_metadata_without_using_cross_process_clock(self):
        worker = IsolatedInferenceWorker("track", {}, worker_target=timed_worker_fixture)
        try:
            self.assertEqual(worker._request(("fixture",)), [])
            value = worker.snapshot()
            self.assertEqual(value["timings"]["jpeg_decode_ms"]["p50_ms"], .5)
            self.assertEqual(value["provider_node_counts"], {"CUDAExecutionProvider": 308})
            self.assertGreaterEqual(value["timings"]["rpc_overhead_ms"]["average_ms"], 0)
        finally:
            worker.close()

    def test_slow_gpu_probe_does_not_block_or_duplicate_and_close_invalidates(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocked(device):
            calls.append(device)
            entered.set()
            release.wait(3)
            return 0, 51, 900

        sampler = CachedGpuSampler(reader=blocked)
        try:
            before = time.monotonic()
            self.assertEqual(sampler.sample("cuda:0"), (None, None, None))
            self.assertLess(time.monotonic() - before, .25)
            self.assertTrue(entered.wait(1))
            for _ in range(10):
                self.assertEqual(sampler.sample("cuda:0"), (None, None, None))
            self.assertEqual(len(calls), 1)
            sampler.close()
        finally:
            release.set()
        self.assertEqual(sampler.sample("cuda:0"), (None, None, None))

    def test_zero_is_valid_but_stale_data_is_not_current(self):
        clock = [10.0]
        sampler = CachedGpuSampler(reader=lambda _: (0, 51, 900), clock=lambda: clock[0])
        # Execute the controlled read directly to avoid scheduler timing in this contract test.
        sampler._device = "cuda:0"
        sampler._read("cuda:0")
        sampler._requested_at = 10
        self.assertEqual(sampler.sample("cuda:0"), (0, 51, 900))
        self.assertEqual(sampler.snapshot()["load_mean_60s_percent"], 0)
        clock[0] = 17
        sampler._pending = True
        self.assertEqual(sampler.sample("cuda:0"), (None, None, None))
        self.assertEqual(sampler.snapshot()["status"], "unavailable")
        sampler.close()

    def test_extended_gpu_sample_exposes_vram_memory_busy_and_thermal_summary(self):
        clock = [100.0]
        values = [
            {"gpu_model": "NVIDIA Test", "gpu_kernel_busy_percent": 20,
             "memory_controller_busy_percent": 10, "temperature_c": 50,
             "dedicated_memory_used_mib": 500, "dedicated_memory_total_mib": 2048,
             "memory_free_mib": 1548, "graphics_clock_mhz": 900,
             "memory_clock_mhz": 500, "power_draw_w": 8.0,
             "power_limit_w": None, "throttle_active_reasons": []},
            {"gpu_model": "NVIDIA Test", "gpu_kernel_busy_percent": 40,
             "memory_controller_busy_percent": 30, "temperature_c": 60,
             "dedicated_memory_used_mib": 700, "dedicated_memory_total_mib": 2048,
             "memory_free_mib": 1348, "graphics_clock_mhz": 1000,
             "memory_clock_mhz": 700, "power_draw_w": 12.0,
             "power_limit_w": None, "throttle_active_reasons": []},
        ]
        sampler = CachedGpuSampler(reader=lambda _: values.pop(0), clock=lambda: clock[0])
        sampler._device = "cuda:0"
        sampler._read("cuda:0")
        clock[0] = 102.0
        sampler._read("cuda:0")
        result = sampler.snapshot()
        self.assertEqual(result["gpu_kernel_busy_percent"], 40)
        self.assertEqual(result["dedicated_memory_used_mib"], 700)
        self.assertEqual(result["dedicated_memory_total_mib"], 2048)
        self.assertEqual(result["load_mean_60s_percent"], 30)
        self.assertEqual(result["memory_busy_max_60s_percent"], 30)
        self.assertEqual(result["temperature_max_60s_c"], 60)
        self.assertEqual(result["dedicated_memory_used_max_60s_mib"], 700)
        self.assertEqual(result["history_coverage_seconds"], 2)
        sampler.close()

    def test_bounded_duration_distributions_do_not_sum_parallel_stages(self):
        stats = StageTimings(capacity=3)
        for value in (1, 2, 3, 4):
            stats.observe({"model_ms": value, "rpc_ms": 10, "unknown": None, "invalid": float("nan")})
        result = stats.snapshot()
        self.assertEqual(result["model_ms"], {"samples": 3, "average_ms": 3, "p50_ms": 3, "p95_ms": 4})
        self.assertNotIn("total", result)
        self.assertNotIn("unknown", result)

    def test_history_trigger_is_not_claimed_as_current_over_budget(self):
        class Analyzer:
            def analyze(self, _frame):
                return FrameAnalysis(False, None, None, "測試")

        wrapper = AdaptiveAnalyzer(Analyzer(), fps=5, logger=lambda _: None)
        wrapper.policy.fps = 3
        wrapper.policy.last_trigger = {"kind": "latency", "previous_fps": 5, "frame_budget_ms": 200}
        wrapper._latencies.extend([.2886] * 5)
        wrapper._completed.extend([100.0] * 4)
        with patch("meowkeeper.core.inference.adaptive_analyzer.time.monotonic", return_value=100):
            value = wrapper.snapshot()
        self.assertFalse(value["currently_over_budget"])
        self.assertTrue(value["degradation_is_historical"])
        self.assertEqual(value["bottleneck_status"], "waiting_or_non_model_work_unresolved")
        with patch("meowkeeper.core.inference.adaptive_analyzer.time.monotonic", return_value=200):
            self.assertIsNone(wrapper.snapshot()["currently_over_budget"])
        wrapper.close()
