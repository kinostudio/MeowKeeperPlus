from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timezone

from meowkeeper.core.detection.frame_analysis import FrameAnalysis
from meowkeeper.core.inference.process_worker import (
    InferenceWorkerError,
    IsolatedInferenceWorker,
    IsolatedTracker,
)
from meowkeeper.core.media.sources import FramePacket


def _test_worker_main(connection, role, config):
    connection.send(("ready", "CPU", "cpu", config.get("backend_diagnostics", {})))
    try:
        while True:
            message = connection.recv()
            if message[0] == "close":
                return
            if message[0] == "reset":
                connection.send(("ok", True, "CPU", "cpu"))
                continue
            if config.get("hang"):
                time.sleep(10)
            result = FrameAnalysis(True, "cat_detected", 0.9, "測試工作程序", {"cat_count": 1})
            connection.send(("ok", result, "CPU", "cpu"))
    except (EOFError, OSError):
        return
    finally:
        connection.close()


def _failure_worker_main(connection, role, config):
    connection.send(("ready", "CPU", "cpu"))
    try:
        message = connection.recv()
        if message[0] != "close":
            connection.send(("error", config.get("failure_type", "RuntimeError")))
    except (EOFError, OSError):
        return
    finally:
        connection.close()


def _frame() -> FramePacket:
    return FramePacket(1, datetime.now(timezone.utc), "test-source", b"\xff\xd8test\xff\xd9")


class IsolatedInferenceWorkerTests(unittest.TestCase):
    def test_tracker_accepts_host_device_contract_without_starting_worker(self):
        worker = IsolatedTracker("models/yolo11n.pt", confidence_threshold=.45, input_size=640,
                                 device="intel:gpu")
        self.assertEqual(worker.config["device"], "intel:gpu")
        self.assertFalse(worker.alive)
        worker.close()

    def test_detect_runs_in_child_process_and_returns_result(self):
        worker = IsolatedInferenceWorker("detect", {}, startup_timeout=10,
                                         inference_timeout=5, worker_target=_test_worker_main)
        try:
            result = worker.analyze(_frame())
            self.assertTrue(result.triggered)
            self.assertNotEqual(worker.process.pid, os.getpid())
            self.assertEqual(worker.snapshot()["isolation"], "process")
            self.assertTrue(worker.snapshot()["ready"])
            self.assertTrue(worker.alive)
        finally:
            worker.close()
        self.assertFalse(worker.alive)

    def test_worker_snapshot_preserves_only_safe_accelerator_failure_diagnostics(self):
        worker = IsolatedInferenceWorker(
            "detect", {"backend_diagnostics": {
                "requested_device": "intel:gpu", "status": "fallback",
                "stage": "gpu_warmup_640", "error_type": "RuntimeError",
                "private_path": "C:\\Users\\user\\secret",
            }}, startup_timeout=10, inference_timeout=5, worker_target=_test_worker_main,
        )
        try:
            worker.analyze(_frame())
            diagnostics = worker.snapshot()["backend_diagnostics"]
            self.assertEqual(diagnostics["stage"], "gpu_warmup_640")
            self.assertEqual(diagnostics["error_type"], "RuntimeError")
            self.assertNotIn("private_path", diagnostics)
            self.assertEqual(worker.snapshot()["backend"], "CPU")
        finally:
            worker.close()

    def test_worker_failure_exposes_only_validated_exception_class(self):
        worker = IsolatedInferenceWorker("detect", {"failure_type": "RuntimeError"},
                                         startup_timeout=10, inference_timeout=5,
                                         worker_target=_failure_worker_main)
        try:
            with self.assertRaises(InferenceWorkerError) as caught:
                worker.analyze(_frame())
            self.assertEqual(caught.exception.failure_type, "RuntimeError")
            self.assertEqual(worker.snapshot()["last_failure_type"], "RuntimeError")
        finally:
            worker.close()

    def test_worker_failure_discards_non_identifier_details(self):
        worker = IsolatedInferenceWorker(
            "detect", {"failure_type": "RuntimeError C:\\private\\path"},
            startup_timeout=10, inference_timeout=5, worker_target=_failure_worker_main,
        )
        try:
            with self.assertRaises(InferenceWorkerError) as caught:
                worker.analyze(_frame())
            self.assertEqual(caught.exception.failure_type, "WorkerError")
            self.assertNotIn("C:\\private", str(caught.exception))
            self.assertEqual(worker.snapshot()["last_failure_type"], "WorkerError")
        finally:
            worker.close()

    def test_first_failure_type_survives_restart_limit(self):
        worker = IsolatedInferenceWorker(
            "detect", {"failure_type": "ValueError"}, startup_timeout=10,
            inference_timeout=5, restart_limit=1, worker_target=_failure_worker_main,
        )
        try:
            for _ in range(2):
                with self.assertRaises(InferenceWorkerError):
                    worker.analyze(_frame())
            with self.assertRaises(InferenceWorkerError) as caught:
                worker.analyze(_frame())
            snapshot = worker.snapshot()
            self.assertEqual(snapshot["last_failure_type"], "WorkerRestartLimit")
            self.assertEqual(snapshot["first_failure_type"], "ValueError")
            self.assertEqual(caught.exception.failure_type, "WorkerRestartLimit")
        finally:
            worker.close()

    def test_timeout_kills_only_worker_and_next_request_restarts_it(self):
        worker = IsolatedInferenceWorker("detect", {"hang": True}, startup_timeout=10,
                                         inference_timeout=0.25, worker_target=_test_worker_main)
        try:
            worker._start()
            stalled_process = worker.process
            with self.assertRaisesRegex(InferenceWorkerError, "逾時"):
                worker.analyze(_frame())
            self.assertFalse(worker.alive)
            self.assertEqual(worker.snapshot()["last_failure_type"], "InferenceTimeout")
            self.assertIsNotNone(stalled_process.exitcode)
            self.assertEqual(worker.snapshot()["restart_count"], 0)
            worker.config["hang"] = False
            result = worker.analyze(_frame())
            self.assertTrue(result.triggered)
            self.assertTrue(worker.alive)
            self.assertEqual(worker.snapshot()["restart_count"], 1)
        finally:
            worker.close()
        self.assertFalse(worker.alive)

    def test_restart_limit_is_enforced_after_configured_restarts(self):
        worker = IsolatedInferenceWorker("detect", {"hang": True}, startup_timeout=10,
                                         inference_timeout=0.25, restart_limit=1,
                                         worker_target=_test_worker_main)
        try:
            with self.assertRaisesRegex(InferenceWorkerError, "逾時"):
                worker.analyze(_frame())
            worker.config["hang"] = False
            self.assertTrue(worker.analyze(_frame()).triggered)
            self.assertEqual(worker.snapshot()["restart_count"], 1)
            worker._stop()
            with self.assertRaisesRegex(InferenceWorkerError, "重啟上限"):
                worker.analyze(_frame())
            self.assertFalse(worker.alive)
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
