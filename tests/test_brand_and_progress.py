"""Brand icons, single-entry launcher and update step progress contracts."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from PIL import Image

from meowkeeper.tray.main import _icon_frame

ROOT = Path(__file__).resolve().parents[1]
STATES = ("ready", "waiting", "error")


class BrandIconTests(unittest.TestCase):
    def test_tray_icons_contain_hand_tuned_small_frames(self) -> None:
        for name in ("meowkeeper", *(f"meowkeeper-earth-{state}" for state in STATES)):
            icon = Image.open(ROOT / "assets/tray" / f"{name}.ico")
            self.assertTrue({(16, 16), (20, 20), (24, 24), (32, 32), (256, 256)} <= set(icon.info["sizes"]), name)

    def test_tray_uses_matching_frame_not_shrunken_artwork(self) -> None:
        for size, expected in ((16, 16), (20, 20), (24, 24), (32, 32), (40, 48)):
            frame = _icon_frame(Image.open(ROOT / "assets/tray/meowkeeper-earth-ready.ico"), size)
            self.assertEqual(frame.size, (expected, expected))

    def test_states_are_distinguishable_at_16_px(self) -> None:
        corners = set()
        for state in STATES:
            frame = _icon_frame(Image.open(ROOT / f"assets/tray/meowkeeper-earth-{state}.ico"), 16)
            corners.add(frame.getpixel((12, 12))[:3])
        self.assertEqual(len(corners), 3)

    def test_web_and_shortcuts_use_the_new_logo(self) -> None:
        base = (ROOT / "src/meowkeeper/app/web/templates/base.html").read_text(encoding="utf-8")
        self.assertIn("/static/icons/meowkeeper-logo.svg", base)
        manifest = json.loads((ROOT / "src/meowkeeper/app/web/static/manifest.json").read_text(encoding="utf-8"))
        self.assertTrue({"192x192", "512x512"} <= {icon["sizes"] for icon in manifest["icons"]})
        for icon in manifest["icons"]:
            self.assertTrue((ROOT / "src/meowkeeper/app/web" / icon["src"].lstrip("/")).is_file())
        for script in ("scripts/Install-MeowKeeperPlus.ps1", "scripts/New-PortableShortcut.ps1"):
            self.assertIn("assets\\tray\\meowkeeper.ico", (ROOT / script).read_text(encoding="utf-8-sig"))


class UpdateStepProgressTests(unittest.TestCase):
    def test_installer_reports_steps_3_to_8_in_order_before_each_phase(self) -> None:
        installer = (ROOT / "scripts/Install-MeowKeeperPlus.ps1").read_text(encoding="utf-8-sig")
        positions = [installer.index(f"Write-InstallStep {step} ") for step in range(3, 9)]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(installer.index("Write-InstallStep 3 "), installer.index("Stop-VerifiedRepositoryTray $packageRoot\n}"))
        self.assertLess(installer.index("Write-InstallStep 6 "), installer.index('Copy-Item -Path (Join-Path $packageRoot "*")'))

    def test_worker_relays_steps_while_waiting_and_keeps_history(self) -> None:
        worker = (ROOT / "scripts/Apply-MeowKeeperPlusUpdate.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Set-UpdateStep 1 ", worker)
        self.assertIn("Set-UpdateStep 2 ", worker)
        self.assertIn("(Join-Path $workRoot 'installer-progress.json')", worker)
        self.assertRegex(worker, r"while \(-not \$process\.WaitForExit\(1000\)")
        self.assertIn("$payload.steps = @($script:stepHistory)", worker)
        script = (ROOT / "src/meowkeeper/app/web/static/js/updates.js").read_text(encoding="utf-8")
        self.assertIn("最後回報", script)
        self.assertTrue(re.search(r"第 \$\{step\.step\}／\$\{step\.step_total\} 步", script))


if __name__ == "__main__":
    unittest.main()
