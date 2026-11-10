"""Every saved runtime setting is editable in the UI and reaches the analyzers."""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

from meowkeeper.app.source_config import _RUNTIME_SETTING_RANGES, RUNTIME_SETTING_DEFAULTS
from meowkeeper.app.web.contracts import SettingsValidationError, validate_settings_patch
from meowkeeper.core.events.v02_host_analyzer import V02HostAnalyzer
from meowkeeper.core.inference.adaptive_analyzer import AdaptiveAnalyzer

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/meowkeeper/app/web"
# Stored for compatibility but not acted on: the single launcher always opens the Dashboard.
NOT_USER_FACING = {"dashboard_auto_open"}
NEW_KEYS = ("behavior_eating_seconds", "behavior_drinking_seconds", "behavior_litter_seconds",
            "behavior_sleep_seconds", "ai_load_target_percent", "cpu_warning_c", "cpu_critical_c",
            "gpu_warning_c", "gpu_critical_c")


class SettingsCoverageTests(unittest.TestCase):
    def test_every_runtime_setting_has_a_ui_control(self) -> None:
        scripts = "".join(path.read_text(encoding="utf-8") for path in (WEB / "static/js").glob("*.js"))
        missing = [key for key in RUNTIME_SETTING_DEFAULTS if key not in NOT_USER_FACING and key not in scripts]
        self.assertEqual(missing, [])

    def test_new_thresholds_have_defaults_ranges_and_validation(self) -> None:
        for key in NEW_KEYS:
            self.assertIn(key, RUNTIME_SETTING_DEFAULTS)
            self.assertIn(key, _RUNTIME_SETTING_RANGES)
        cleaned = validate_settings_patch({"behavior_eating_seconds": 12, "cpu_warning_c": 80, "cpu_critical_c": 90})
        self.assertEqual(cleaned["behavior_eating_seconds"], 12.0)
        for bad in ({"behavior_eating_seconds": 0}, {"ai_load_target_percent": 95},
                    {"gpu_warning_c": 85, "gpu_critical_c": 85}, {"cpu_warning_c": True}):
            with self.subTest(bad=bad), self.assertRaises(SettingsValidationError):
                validate_settings_patch(bad)

    def test_saved_load_and_temperature_limits_reach_the_policy(self) -> None:
        analyzer = AdaptiveAnalyzer(SimpleNamespace(inference_backend="CPU"), fps=5, logger=lambda _message: None,
                                    thresholds={"ai_load_target_percent": 55.0, "cpu_warning_c": 80.0,
                                                "cpu_critical_c": 90.0, "gpu_warning_c": 75.0, "gpu_critical_c": 82.0})
        settings = analyzer.policy.settings
        self.assertEqual((settings.target_percent, settings.cpu_warning_c, settings.cpu_critical_c,
                          settings.gpu_warning_c, settings.gpu_critical_c), (55.0, 80.0, 90.0, 75.0, 82.0))
        fallback = AdaptiveAnalyzer(SimpleNamespace(inference_backend="CPU"), fps=5, logger=lambda _message: None,
                                    thresholds={"cpu_warning_c": 99.0, "cpu_critical_c": 90.0})
        self.assertEqual(fallback.policy.settings.cpu_warning_c, 85)

    def test_behavior_thresholds_reach_the_v02_analyzer(self) -> None:
        self.assertIn("behavior_config", inspect.signature(V02HostAnalyzer.create).parameters)
        from meowkeeper.app.core_controller import RepositoryCoreController

        controller = RepositoryCoreController.__new__(RepositoryCoreController)
        controller.behavior_thresholds = {"behavior_eating_seconds": 14.0, "behavior_sleep_seconds": 90.0}
        controller._log = lambda _message: None
        config = controller._behavior_config()
        self.assertEqual((config.minimum_eating_seconds, config.sleeping_minimum_seconds), (14.0, 90.0))
        controller.behavior_thresholds = {"behavior_eating_seconds": -1.0}
        self.assertEqual(controller._behavior_config().minimum_eating_seconds, 10.0)
        host = (ROOT / "tools/run_host.py").read_text(encoding="utf-8")
        self.assertIn("behavior_thresholds=", host)
        self.assertIn("adaptive_thresholds=", host)


if __name__ == "__main__":
    unittest.main()
