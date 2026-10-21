from __future__ import annotations

import unittest

from tools.benchmark_gpu_inference import (
    _advance_schedule_deadline,
    _aggregate_size_runs,
    _align_schedule_deadline,
    _check_safety,
    _distribution,
    _initial_telemetry,
    _nvidia_sample_required,
    _round_schedule,
)


class GpuBenchmarkTests(unittest.TestCase):
    def test_latency_summary_reports_sample_count_and_nearest_rank_p95(self):
        result = _distribution([100, 200, 300, 400, 500])
        self.assertEqual(result, {"samples": 5, "average_ms": 300, "p50_ms": 300,
                                  "p90_ms": 500, "p95_ms": 500, "p99_ms": 500,
                                  "max_ms": 500})

    def test_schedule_drops_expired_slots_without_catch_up_bursts(self):
        deadline, missed, lag = _align_schedule_deadline(10.0, 10.25, .2)
        self.assertEqual(missed, 1)
        self.assertAlmostEqual(deadline, 10.25)
        self.assertAlmostEqual(lag, .05)
        deadline, missed, lag = _align_schedule_deadline(deadline, 10.25, .2)
        self.assertEqual(missed, 0)
        self.assertEqual(lag, 0)
        self.assertAlmostEqual(deadline, 10.25)

    def test_schedule_does_not_skip_a_frame_for_subperiod_lateness(self):
        deadline, missed, lag = _align_schedule_deadline(10.0, 10.19, .2)
        self.assertEqual(missed, 0)
        self.assertAlmostEqual(deadline, 10.19)
        self.assertAlmostEqual(lag, .19)

    def test_late_start_gets_a_full_period_before_the_next_frame(self):
        deadline, missed, _ = _align_schedule_deadline(10.0, 10.19, .2)
        next_deadline, overrun = _advance_schedule_deadline(deadline, 10.24, .2)
        self.assertEqual(missed, 0)
        self.assertEqual(overrun, 0)
        self.assertAlmostEqual(next_deadline, 10.39)

    def test_schedule_rebases_after_overrun_without_counting_late_frame_as_dropped(self):
        deadline, missed = _advance_schedule_deadline(10.0, 10.35, .2)
        self.assertEqual(missed, 0)
        self.assertEqual(deadline, 10.35)

    def test_schedule_counts_each_overrun_slot(self):
        deadline, missed = _advance_schedule_deadline(10.0, 10.45, .2)
        self.assertEqual(missed, 1)
        self.assertEqual(deadline, 10.45)
        deadline, missed = _advance_schedule_deadline(10.0, 10.65, .2)
        self.assertEqual(missed, 2)
        self.assertEqual(deadline, 10.65)

    def test_round_schedule_reverses_order_to_reduce_position_bias(self):
        self.assertEqual(_round_schedule([960, 1280, 1920], 3), [
            [960, 1280, 1920], [1920, 1280, 960], [960, 1280, 1920],
        ])

    def test_intel_telemetry_is_not_mislabeled_from_nvidia_sampler(self):
        class SamplerMustNotRun:
            def sample(self, _device):
                raise AssertionError("Intel runs must not sample NVIDIA telemetry")

        model = type("Model", (), {"device": "intel:gpu", "backend": "OpenVINO GPU"})()
        self.assertFalse(_nvidia_sample_required(model))
        sample = _initial_telemetry(SamplerMustNotRun(), model)
        self.assertEqual(sample["status"], "not_measured")
        self.assertIn("Intel GPU", sample["scope"])

    def test_cpu_telemetry_is_not_mislabeled_from_nvidia_sampler(self):
        class SamplerMustNotRun:
            def sample(self, _device):
                raise AssertionError("CPU runs must not sample NVIDIA telemetry")

        model = type("Model", (), {"device": "cpu", "backend": "CPU"})()
        self.assertFalse(_nvidia_sample_required(model))
        sample = _initial_telemetry(SamplerMustNotRun(), model)
        self.assertEqual(sample["status"], "not_measured")
        self.assertIn("CPU", sample["scope"])

    def test_aggregate_p95_pools_samples_instead_of_averaging_run_percentiles(self):
        runs = [
            ({"duration_seconds": 10, "completed_frames": 10, "missed_schedule_slots": 0,
              "observed_throttle_reasons": {}, "stop_reason": None},
             {"schedule_lag_ms": [1], "timings": {"isolated_total_ms": [10, 10, 10]},
              "cpu_values": [1], "rss_values": [100],
              "telemetry_values": {"gpu_kernel_busy_percent": [20]}}),
            ({"duration_seconds": 10, "completed_frames": 20, "missed_schedule_slots": 1,
              "observed_throttle_reasons": {}, "stop_reason": None},
             {"schedule_lag_ms": [2], "timings": {"isolated_total_ms": [10, 10, 100]},
              "cpu_values": [2], "rss_values": [200],
              "telemetry_values": {"gpu_kernel_busy_percent": [40]}}),
        ]
        result = _aggregate_size_runs(960, 5, runs)
        self.assertEqual(result["actual_fps"], 1.5)
        self.assertEqual(result["timings"]["isolated_total_ms"]["samples"], 6)
        self.assertEqual(result["timings"]["isolated_total_ms"]["p95_ms"], 100)
        self.assertEqual(result["timings"]["isolated_total_ms"]["p99_ms"], 100)

    def test_benchmark_stops_if_cuda_thermal_monitoring_is_missing(self):
        result = _check_safety({"temperature_c": None, "memory_free_mib": 2048},
                               require_temperature=True)
        self.assertIn("溫度無法取得", result)

    def test_benchmark_stops_at_thermal_or_vram_boundary_but_not_soft_load_target(self):
        hot = _check_safety({"temperature_c": 80, "memory_free_mib": 2048}, require_temperature=True)
        low_memory = _check_safety({"temperature_c": 60, "memory_free_mib": 127},
                                   require_temperature=True)
        normal = _check_safety({"temperature_c": 60, "memory_free_mib": 2048,
                                "gpu_kernel_busy_percent": 95}, require_temperature=True)
        self.assertIsNotNone(hot)
        self.assertIsNotNone(low_memory)
        self.assertIsNone(normal)  # 40% is a soft long-term target, not a hard trip point.


if __name__ == "__main__":
    unittest.main()
