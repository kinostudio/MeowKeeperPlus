"""r121 UI shell: status tag, five-place navigation, onboarding guide and field tooltips."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

WEB = Path(__file__).resolve().parents[1] / "src" / "meowkeeper" / "app" / "web"
TEMPLATES = WEB / "templates"
STATIC = WEB / "static"


def render(name: str, page_path: str) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    return env.get_template(name).render(page_path=page_path, page_title="測試", term_help_terms={})


class ShellTests(unittest.TestCase):
    def test_top_right_shows_runtime_status_not_tray(self):
        for path in ("/", "/live", "/settings/ai"):
            html = render("base.html", path)
            self.assertIn('id="runtime-status-tag"', html)
            self.assertIn('role="status"', html)
            self.assertNotIn("Tray 已連線", html)
            self.assertIn("/static/js/status-tag.js", html)
            self.assertIn("/static/css/warm.css", html)

    def test_five_primary_places_and_usage_guide_entry(self):
        html = render("base.html", "/settings/telegram")
        primaries = re.findall(r'class="nav-primary[^"]*"[^>]*>.*?<span class="nav-link-text">([^<]+)</span>', html, re.DOTALL)
        self.assertEqual(primaries, ["首頁", "即時畫面", "事件", "貓咪", "設定"])
        self.assertIn('href="/help"', html)
        self.assertIn("使用說明", html)
        self.assertIn("data-guide-reopen", html)
        self.assertEqual(html.count('class="active"'), 1)
        self.assertIn("in-section", html)

    def test_warm_css_uses_local_fonts_only(self):
        css = (STATIC / "css" / "warm.css").read_text(encoding="utf-8")
        self.assertNotIn("googleapis", css)
        self.assertNotIn("@import", css)
        for state in ("ok", "warn", "alert", "off"):
            self.assertIn(f"[data-state={state}]", css)

    def test_home_guide_has_five_steps_with_real_routes(self):
        html = (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
        guide = html[html.index('id="home-guide"'):html.index("</section>", html.index('id="home-guide"'))]
        self.assertEqual(guide.count("<li"), 5)
        for route in ("/settings/source", "/settings/pets", "/settings/ai#scene-registry-panel", "/settings/telegram", "/help"):
            self.assertIn(f'href="{route}"', guide)
        self.assertIn('id="home-guide-dismiss"', guide)
        self.assertIn('id="home-status-hero"', html)
        settings = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
        self.assertIn('id="scene-registry-panel"', settings)

    def test_field_tooltips_are_accessible(self):
        js = (STATIC / "js" / "field-tooltips.js").read_text(encoding="utf-8")
        self.assertIn("aria-describedby", js)
        self.assertIn('role", "tooltip"', js)
        self.assertIn("Escape", js)
        self.assertIn("event.preventDefault()", js)  # button inside <label> must not toggle the control
        self.assertIn("mouseenter", js)
        self.assertIn('"focus"', js)
        for page in ("base.html", "login.html", "setup.html"):
            self.assertIn("field-tooltips.js", (TEMPLATES / page).read_text(encoding="utf-8"))
        css = (STATIC / "css" / "term-help.css").read_text(encoding="utf-8")
        self.assertIn(".field-tip-anchor.open .field-tip", css)
        self.assertIn("@media (hover:none)", css)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class StatusClassifyTests(unittest.TestCase):
    def classify(self, payload):
        script = (STATIC / "js" / "status-tag.js").read_text(encoding="utf-8")
        harness = (
            "const window={location:{search:''},localStorage:null};"
            "const document={readyState:'complete',addEventListener(){},getElementById(){return null},querySelectorAll(){return []}};"
            + script
            + f";console.log(JSON.stringify(window.MeowKeeperStatusTag.classify({json.dumps(payload)})));"
        )
        out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True, timeout=30)
        return json.loads(out.stdout)["state"]

    def test_four_states(self):
        now = "2026-11-10T12:00:00Z"
        fresh = {"status": "running", "last_frame_at": "2026-11-10T11:59:58Z"}
        self.assertEqual(self.classify(None), "alert")
        self.assertEqual(self.classify({"status": "stopped"}), "off")
        self.assertEqual(self.classify({"status": "starting"}), "warn")
        self.assertEqual(self.classify({"status": "running", "checked_at": now, "metrics": {"source": {"status": "running", "last_frame_at": "2026-11-10T11:58:00Z"}}}), "alert")
        self.assertEqual(self.classify({"status": "running", "checked_at": now, "metrics": {"source": fresh, "ai": {"fps": 0}}}), "warn")
        self.assertEqual(self.classify({"status": "running", "checked_at": now, "metrics": {"source": fresh, "ai": {"fps": 2.5}}}), "ok")


if __name__ == "__main__":
    unittest.main()
