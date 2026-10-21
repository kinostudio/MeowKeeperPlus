from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.benchmark_candidate_pipeline import (
    _acceptance_failures,
    _candidate_preflight_failures,
    _json_safe,
    _measurement_snapshot,
)


class CandidatePipelineBenchmarkTests(unittest.TestCase):
    def test_frame_and_backend_label_without_completed_inference_does_not_pass(self):
        failures = _acceptance_failures(
            first_frame_elapsed=1.2,
            first_inference_elapsed=None,
            host_state="running",
            source_status="running",
            source_connections=1,
            actual_backend="ONNX Runtime CUDA",
            actual_device="cuda:0",
            measured_fps=None,
            queue_metrics={"completed": 0, "failed": 28},
            worker={"provider_node_counts": {}},
            device_requested="onnx:cuda",
        )

        self.assertIn("fewer_than_three_completed_inferences", failures)
        self.assertIn("no_completed_analysis_fps", failures)
        self.assertIn("no_cuda_execution_node_evidence", failures)

    def test_cuda_run_requires_completed_work_on_cuda_nodes(self):
        failures = _acceptance_failures(
            first_frame_elapsed=1.2,
            first_inference_elapsed=2.4,
            host_state="running",
            source_status="running",
            source_connections=1,
            actual_backend="ONNX Runtime CUDA",
            actual_device="cuda:0",
            measured_fps=1.0,
            queue_metrics={"completed": 12, "failed": 0},
            worker={"provider_node_counts": {"CUDAExecutionProvider": 308}},
            device_requested="onnx:cuda",
        )

        self.assertEqual(failures, [])

    def test_intel_run_requires_completed_work_on_requested_backend(self):
        failures = _acceptance_failures(
            first_frame_elapsed=1.2,
            first_inference_elapsed=2.4,
            host_state="running",
            source_status="running",
            source_connections=1,
            actual_backend="OpenVINO GPU",
            actual_device="intel:gpu",
            measured_fps=1.0,
            queue_metrics={"completed": 12, "failed": 0},
            worker={"provider_node_counts": {}},
            device_requested="intel:gpu",
        )

        self.assertEqual(failures, [])

    def test_snapshot_falls_back_to_core_reported_analysis_fps(self):
        controller = SimpleNamespace(get_metrics=lambda: {
            "status": "running",
            "metrics": {"ai": {"fps": 2.4, "adaptive": {"effective_fps": 5}}},
        })

        self.assertEqual(_measurement_snapshot(controller)["ai"]["measured_fps"], 2.4)

    def test_snapshot_prefers_explicit_adaptive_fps_when_available(self):
        controller = SimpleNamespace(get_metrics=lambda: {
            "status": "running",
            "metrics": {"ai": {"fps": 2.4, "adaptive": {"measured_fps": 1.8}}},
        })

        self.assertEqual(_measurement_snapshot(controller)["ai"]["measured_fps"], 1.8)

    def test_snapshot_reads_live_analyzer_while_camera_loop_waits_for_first_inference(self):
        live = SimpleNamespace(
            inference_backend="OpenVINO GPU",
            device="intel:gpu",
            snapshot=lambda: {
                "effective_input_size": 960,
                "effective_fps": 5,
                "measured_fps": 0,
                "worker": {"alive": True, "ready": False, "last_failure_type": None},
                "host_scheduler_queue_metrics": {"completed": 0, "failed": 0},
            },
        )
        controller = SimpleNamespace(
            _adaptive_analyzer=live,
            get_metrics=lambda: {"status": "running", "metrics": {"ai": {}}},
        )

        snapshot = _measurement_snapshot(controller)["ai"]

        self.assertEqual(snapshot["backend"], "OpenVINO GPU")
        self.assertEqual(snapshot["device"], "intel:gpu")
        self.assertFalse(snapshot["worker_ready"])
        self.assertEqual(snapshot["inference_completed_count"], 0)

    def test_fallback_to_cpu_fails_even_if_cpu_inferences_completed(self):
        failures = _acceptance_failures(
            first_frame_elapsed=1.2,
            first_inference_elapsed=2.4,
            host_state="running",
            source_status="running",
            source_connections=1,
            actual_backend="CPU",
            actual_device="cpu",
            measured_fps=1.0,
            queue_metrics={"completed": 12, "failed": 0},
            worker={"provider_node_counts": {}},
            device_requested="intel:gpu",
        )

        self.assertIn("requested_accelerator_not_active_at_end", failures)

    def test_non_finite_telemetry_is_serializable_null(self):
        self.assertEqual(_json_safe({"nan": math.nan, "inf": math.inf}),
                         {"nan": None, "inf": None})

    def test_preflight_rejects_candidate_missing_botsort_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate_root = Path(temp_dir)
            (candidate_root / "configs").mkdir()

            self.assertEqual(_candidate_preflight_failures(candidate_root),
                             [str(Path("configs") / "botsort_reid.yaml")])

    def test_preflight_accepts_candidate_with_botsort_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate_root = Path(temp_dir)
            config = candidate_root / "configs" / "botsort_reid.yaml"
            config.parent.mkdir()
            config.write_text("tracker_type: botsort\n", encoding="utf-8")

            self.assertEqual(_candidate_preflight_failures(candidate_root), [])


if __name__ == "__main__":
    unittest.main()
