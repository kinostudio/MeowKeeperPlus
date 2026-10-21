"""Render the real templates to protect navigation, IDs and accessible help."""
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "src/meowkeeper/app/web/templates"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.hints = []
        self.scripts = []
        self.active_links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if "data-help-for" in attrs:
            self.hints.append(attrs["data-help-for"])
        if tag == "script" and "src" in attrs:
            self.scripts.append(attrs["src"])
        if tag == "a" and "active" in attrs.get("class", "").split():
            self.active_links.append(attrs.get("href"))


class DashboardExperienceTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"])
        )

    def render(self, template, path):
        html = self.env.get_template(template).render(
            page_title="驗收頁", page_path=path, setup_token="test-only"
        )
        parsed = Elements()
        parsed.feed(html)
        return html, parsed

    def test_pages_keep_unique_targets_and_local_scripts(self):
        pages = [("dashboard.html", "/dashboard"), ("live.html", "/live"),
                 ("events.html", "/events"), ("xiaomi_setup.html", "/cameras/xiaomi/setup"),
                 ("analytics.html", "/analytics"), ("logs.html", "/logs"),
                 ("diagnostics.html", "/diagnostics")]
        pages += [("settings.html", "/settings/" + page)
                  for page in ("source", "ai", "telegram", "tailscale", "advanced")]
        for template, path in pages:
            with self.subTest(path=path):
                html, parsed = self.render(template, path)
                self.assertFalse([key for key, count in Counter(parsed.ids).items() if count > 1])
                self.assertIn("primary-nav", parsed.ids)
                self.assertIn("main-content", parsed.ids)
                self.assertIn("host-restart", parsed.ids)
                self.assertEqual(len(parsed.active_links), 1)
                self.assertTrue(all(src.startswith("/static/") for src in parsed.scripts))
                for field in parsed.hints:
                    self.assertIn(field, parsed.ids)
                    self.assertIn(field + "-help", parsed.ids)
                self.assertIn("跳至主要內容", html)

    def test_ai_parameters_have_individual_help(self):
        _, parsed = self.render("settings.html", "/settings/ai")
        for key in ("confidence-threshold", "analysis-fps", "activity-fps", "cooldown-seconds",
                    "event-pre-seconds", "event-post-seconds", "retention-seconds",
                    "ring-maximum-size-mb"):
            self.assertIn("ai-" + key, parsed.hints)

    def test_behavior_scene_registry_keeps_drag_upload_snapshot_and_local_collage(self):
        html, parsed = self.render("settings.html", "/settings/ai")
        for element_id in ("scene-registry-panel", "scene-current-preview", "scene-frame-confirm",
                           "scene-save-view", "scene-reference-file", "scene-upload-reference",
                           "scene-object-canvas", "scene-object-name", "scene-object-kind",
                           "scene-save-object", "scene-preview-collage", "scene-collage-preview"):
            self.assertIn(element_id, parsed.ids)
        self.assertIn("/static/js/scene-registry.js", parsed.scripts)
        self.assertIn("直接拖曳圈出", html)
        self.assertIn("預覽 API 參考拼貼", html)
        self.assertIn("預設只保存在本機", html)
        self.assertNotIn('id="zone-x"', html)
        self.assertNotIn('id="zone-width"', html)
        self.assertNotIn("X（%）", html)

    def test_dashboard_retains_live_update_targets(self):
        _, parsed = self.render("dashboard.html", "/dashboard")
        for name in ("monitor", "source", "ffmpeg", "ai", "ring", "telegram", "sqlite", "system", "hardware", "tailscale"):
            self.assertIn(name + "-status", parsed.ids)
            self.assertIn(name + "-detail", parsed.ids)
        self.assertIn("events-list", parsed.ids)
        self.assertIn("service-state", parsed.ids)

    def test_fullscreen_preview_is_not_limited_to_normal_page_height(self):
        html, parsed = self.render("live.html", "/live")
        self.assertIn("preview-fullscreen", parsed.ids)
        self.assertIn("requestFullscreen", html)
        css = (ROOT / "src/meowkeeper/app/web/static/css/experience.css").read_text(encoding="utf-8")
        self.assertIn(".preview-stage:fullscreen", css)
        self.assertIn("max-height:100%; object-fit:contain", css)


if __name__ == "__main__":
    unittest.main()
