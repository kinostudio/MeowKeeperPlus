from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliHelpTests(unittest.TestCase):
    def test_all_phase_zero_tools_have_help(self) -> None:
        for script_name in (
            "system_probe.py",
            "camera_probe.py",
            "telegram_probe.py",
            "ffmpeg_probe.py",
            "youtube_probe.py",
            "gpu_probe.py",
            "run_phase0.py",
            "run_monitor.py",
            "fetch_model.py",
            "fetch_identity_model.py",
            "enroll_pet.py",
            "export_behavior_dataset.py",
            "train_behavior_model.py",
            "evaluate_behavior_model.py",
            "run_v02.py",
            "build_portable.py",
            "create_task_scheduler_xml.py",
            "verify_portable.py",
            "verify_windows_installation.py",
            "run_web.py",
            "run_tray.py",
        ):
            with self.subTest(script=script_name):
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "tools" / script_name), "--help"],
                    capture_output=True,
                    check=False,
                    shell=False,
                    text=True,
                    timeout=10,
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("usage:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
