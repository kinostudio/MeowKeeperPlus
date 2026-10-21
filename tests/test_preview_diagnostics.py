import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from meowkeeper.core.events.v02_host_analyzer import V02HostAnalyzer
from meowkeeper.core.inference.adaptive_policy import AdaptivePolicy, AdaptiveSettings
from meowkeeper.core.media.annotated_export import annotation_filters
from meowkeeper.core.media.shared_file import read_shared_bytes


class PreviewDiagnosticsTests(unittest.TestCase):
    def test_overlay_never_crosses_monitor_epochs(self):
        from meowkeeper.app.web.state import CoreStateFacade

        state = {"metrics": {"source": {"preview_epoch": "new-run"},
                             "ai": {"overlay": {"source_epoch": "old-run", "detections": []}}}}
        facade = CoreStateFacade(callbacks={"status": lambda: state})
        self.assertIsNone(facade.preview_status()["overlay"])
        state["metrics"]["ai"]["overlay"]["source_epoch"] = "new-run"
        self.assertIsNotNone(facade.preview_status()["overlay"])

    def test_sampling_is_information_and_repeated_warnings_are_aggregated(self):
        analyzer = object.__new__(V02HostAnalyzer)
        analyzer._warning_counts, analyzer._warning_last_log = {}, {}
        logs = []
        analyzer.logger = logs.append
        with patch("meowkeeper.core.events.v02_host_analyzer.time.monotonic", return_value=100):
            for _ in range(10):
                analyzer._report_warning("ℹ️ GPU 負載控制略過一張普通 AI 影格；環形錄影仍持續。")
        self.assertEqual(len(logs), 1)
        self.assertIn("正常保護", logs[0])
        with patch("meowkeeper.core.events.v02_host_analyzer.time.monotonic", return_value=161):
            analyzer._report_warning("略過一張普通 AI 影格")
        self.assertIn("10 次", logs[-1])
        analyzer._report_warning("secret://not-to-be-logged")
        self.assertNotIn("secret://", logs[-1])

    def test_low_temperature_does_not_hide_latency_trigger(self):
        policy = AdaptivePolicy(AdaptiveSettings())
        policy.observe(0, backend="ONNX Runtime CUDA", load_percent=15, temperature_c=53, too_slow=True)
        policy.observe(61, backend="ONNX Runtime CUDA", load_percent=15, temperature_c=53, too_slow=True)
        self.assertEqual(policy.fps, 3)
        self.assertEqual(policy.snapshot()["last_trigger"]["kind"], "latency")
        self.assertIn("不等同 GPU 滿載", policy.reason)

    def test_shared_read_and_repeated_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "latest.jpg"
            path.write_bytes(b"first")
            errors = []
            def read():
                try:
                    for _ in range(200):
                        self.assertIn(read_shared_bytes(path), (b"first", b"next"))
                except (OSError, AssertionError) as error:
                    errors.append(error)
            reader = threading.Thread(target=read)
            reader.start()
            for _ in range(100):
                temporary = Path(root) / "pending.jpg"
                temporary.write_bytes(b"next")
                for attempt in range(10):
                    try:
                        temporary.replace(path)
                        break
                    except PermissionError:
                        if attempt == 9:
                            raise
                        time.sleep(.01)
            reader.join(5)
            self.assertFalse(reader.is_alive())
            self.assertFalse(errors, errors)

    def test_annotation_filter_is_bounded_and_numeric_only(self):
        filters = annotation_filters([(1.0, [[.1, .2, .4, .5], [float("nan"), 0, 1, 1]])])
        self.assertEqual(filters.count("drawbox="), 1)
        self.assertIn("lt(t,1.500)", filters)
        self.assertNotIn("nan", filters)
        self.assertEqual(annotation_filters([]), "null")
        with self.assertRaises(ValueError):
            annotation_filters([(float("inf"), [])])
