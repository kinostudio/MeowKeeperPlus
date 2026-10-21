import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meowkeeper.app.source_config import save_runtime_settings
from tools.run_host import _build_runtime, build_parser, configure_cpu_wait_policy


class HostAnalyzerSettingsTests(unittest.TestCase):
    def test_cpu_wait_policy_defaults_and_explicit_overrides(self):
        with patch.dict(os.environ, {}, clear=True):
            configure_cpu_wait_policy()
            self.assertEqual(os.environ["OMP_WAIT_POLICY"], "PASSIVE")
            self.assertEqual(os.environ["KMP_BLOCKTIME"], "0")
        with patch.dict(os.environ, {"OMP_WAIT_POLICY": "ACTIVE", "KMP_BLOCKTIME": "50"}, clear=True):
            configure_cpu_wait_policy()
            self.assertEqual(os.environ["OMP_WAIT_POLICY"], "ACTIVE")
            self.assertEqual(os.environ["KMP_BLOCKTIME"], "50")

    def test_saved_mode_and_explicit_override_without_starting_monitor(self):
        for override, expected in ((None, "v02"), ("yolo", "yolo"), ("noop", "noop")):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / "config.json"
                save_runtime_settings(config, {"analyzer_mode": "v02", "identity_frame_width": 960,
                                               "analysis_width": 1280, "inference_device": "intel:gpu"})
                argv = ["--disable-monitor", "--no-open-browser",
                        "--dashboard-config", str(config),
                        "--database-path", str(root / "events.db"),
                        "--event-directory", str(root / "events"),
                        "--ring-directory", str(root / "ring"),
                        "--preview-directory", str(root / "preview"),
                        "--admin-storage-path", str(root / "admin.json"),
                        "--source-url-env", "MEOWKEEPER_TEST_UNUSED_SOURCE"]
                if override:
                    argv += ["--analyzer", override]
                with patch.dict(os.environ, {}, clear=True), patch(
                    "tools.run_host._telegram_options", return_value=(False, None, ())
                ), patch("tools.run_host.TelegramCredentialStore", return_value=None):
                    app, controller, store = _build_runtime(build_parser().parse_args(argv))
                try:
                    self.assertEqual(controller.analyzer_mode, expected)
                    self.assertEqual(controller.identity_frame_width, 960)
                    self.assertEqual(controller.analysis_width, 1280)
                    self.assertEqual(controller.inference_device, "intel:gpu")
                    self.assertFalse(controller.is_running)
                    self.assertIsNotNone(app)
                finally:
                    controller.close()
                    store.close()


if __name__ == "__main__":
    unittest.main()
