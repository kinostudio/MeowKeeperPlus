"""Static front-end wiring: element IDs, API routes and assets referenced by the UI exist."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/meowkeeper/app/web"
# Scripts not loaded by any page; kept guarded (`if (!form) return`).
UNLOADED_SCRIPTS = {"zones.js"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FrontendWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.templates = {path.name: _read(path) for path in (WEB / "templates").glob("*.html")}
        self.scripts = {path.name: _read(path) for path in (WEB / "static").rglob("*.js")}
        html = "".join(self.templates.values())
        js = "".join(self.scripts.values())
        self.defined = set(re.findall(r'\bid="([^"{]+)"', html)) | set(re.findall(r'\.id\s*=\s*["\']([^"\']+)', js))

    def test_every_looked_up_element_exists(self) -> None:
        missing = []
        for name, text in {**self.templates, **self.scripts}.items():
            if name in UNLOADED_SCRIPTS:
                continue
            for element in re.findall(r'getElementById\(\s*["\']([A-Za-z0-9_-]+)["\']\s*\)', text):
                if element not in self.defined:
                    missing.append(f"{name}: #{element}")
        self.assertEqual(missing, [])

    def test_ai_settings_save_sends_event_video_limits(self) -> None:
        settings = self.templates["settings.html"]
        for element in ("ai-event-video-max-age-days", "ai-event-video-max-gb"):
            self.assertIn(f'id="{element}"', settings)
        script = self.scripts["ai.js"]
        self.assertIn("if (input) payload[name] = Number(input.value);", script)
        self.assertNotIn('?.value || 3)', script)

    def test_static_assets_referenced_by_templates_exist(self) -> None:
        missing = [f"{name}: {ref}" for name, text in self.templates.items()
                   for ref in re.findall(r'/static/([^"\'?\s{]+)', text)
                   if not (WEB / "static" / ref).exists()]
        self.assertEqual(missing, [])

    def test_unloaded_scripts_are_really_unloaded(self) -> None:
        html = "".join(self.templates.values())
        for name in UNLOADED_SCRIPTS:
            self.assertNotIn(f"/static/js/{name}", html)


if __name__ == "__main__":
    unittest.main()
