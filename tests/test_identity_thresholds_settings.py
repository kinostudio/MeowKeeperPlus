import tempfile
import unittest
from pathlib import Path

from meowkeeper.app.source_config import load_runtime_settings, save_runtime_settings
from meowkeeper.app.web.contracts import SettingsValidationError, validate_settings_patch, validate_settings_with_pydantic
from meowkeeper.core.identity.base import IdentityThresholds


class IdentityThresholdsSettingsTests(unittest.TestCase):
    def test_source_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            config_file = Path(td) / "config.json"
            config_file.write_text('{"dashboard_settings": {}}', encoding="utf-8")
            
            # Default values
            defaults = load_runtime_settings(config_file)
            self.assertEqual(defaults["identity_accepted_threshold"], 0.62)
            self.assertEqual(defaults["identity_minimum_margin"], 0.05)
            
            # Save custom values
            save_runtime_settings(config_file, {
                "identity_accepted_threshold": 0.75,
                "identity_minimum_margin": 0.08,
            })
            updated = load_runtime_settings(config_file)
            self.assertEqual(updated["identity_accepted_threshold"], 0.75)
            self.assertEqual(updated["identity_minimum_margin"], 0.08)

    def test_contracts_validation(self):
        # Valid
        res = validate_settings_patch({
            "identity_accepted_threshold": 0.7,
            "identity_minimum_margin": 0.1,
        })
        self.assertEqual(res["identity_accepted_threshold"], 0.7)
        self.assertEqual(res["identity_minimum_margin"], 0.1)

        # Invalid out of range
        with self.assertRaises(SettingsValidationError):
            validate_settings_patch({"identity_accepted_threshold": 1.5})
        with self.assertRaises(SettingsValidationError):
            validate_settings_patch({"identity_minimum_margin": -0.1})

        # Pydantic validation if installed
        res_pyd = validate_settings_with_pydantic({
            "identity_accepted_threshold": 0.55,
            "identity_minimum_margin": 0.02,
        })
        self.assertEqual(res_pyd["identity_accepted_threshold"], 0.55)
        self.assertEqual(res_pyd["identity_minimum_margin"], 0.02)


if __name__ == "__main__":
    unittest.main()
