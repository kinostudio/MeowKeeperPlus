"""r122: live / events / settings / phone layouts follow the design without dropping existing controls."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

WEB = Path(__file__).resolve().parents[1] / "src" / "meowkeeper" / "app" / "web"
TEMPLATES = WEB / "templates"
STATIC = WEB / "static"


def render(name: str, page_path: str) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    return env.get_template(name).render(page_path=page_path, page_title="測試", term_help_terms={})


class LiveLayoutTests(unittest.TestCase):
    def test_camera_controls_sit_in_side_column_and_keep_every_control(self):
        html = render("live.html", "/live")
        side = html[html.index('<aside class="live-side"'):html.index("</aside>")]
        for control in ("preview-ptz-directions", "ptz-coordinate-map", "ptz-refresh", "live-ptz-controls",
                        "live-spots", "privacy-mode"):
            self.assertIn(f'id="{control}"', side)
        for action in ("hide", "show", "capture", "set_mode"):
            self.assertIn(f'data-privacy-action="{action}"', side)
        main = html[html.index('<section class="live-main"'):html.index('<aside class="live-side"')]
        for control in ("preview-image", "ptz-position-hud", "live-quality-profile", "live-quality-apply", "capture-result"):
            self.assertIn(f'id="{control}"', main)
        self.assertEqual(main.count("data-save-capture="), 2)

    def test_quick_quality_buttons_drive_existing_select(self):
        html = render("live.html", "/live")
        for choice in ("low", "auto", "high"):
            self.assertIn(f'data-quality-choice="{choice}"', html)
        self.assertIn("apply.click()", html)

    def test_saved_spots_use_verified_restore_endpoint(self):
        html = render("live.html", "/live")
        self.assertIn("/restore-position", html)
        self.assertIn("X-MeowKeeper-CSRF", html)
        self.assertIn("position_binding", html)  # only views with a verified bound position


class EventsLayoutTests(unittest.TestCase):
    def test_filters_pending_and_table(self):
        html = (TEMPLATES / "events.html").read_text(encoding="utf-8")
        for view in ("all", "pending", "today"):
            self.assertIn(f'data-events-view="{view}"', html)
        for element in ("events-pending", "events-pending-list", "events-page-list", "events-kind-filter",
                        "events-previous", "events-next", "event-playback-dialog"):
            self.assertIn(f'id="{element}"', html)
        self.assertIn('class="events-table-head"', html)

    def test_rows_offer_play_download_and_review(self):
        js = (STATIC / "js" / "event-list.js").read_text(encoding="utf-8")
        self.assertIn("openPlayback(item)", js)
        self.assertIn('download.setAttribute("download","")', js)
        self.assertIn('"meowkeeper:review"', js)
        self.assertIn("has_human_review", js)
        for state in ("ok", "warn", "alert", "off"):
            self.assertIn(f'["{state}"', js)


class SettingsLayoutTests(unittest.TestCase):
    def test_overview_route_and_cards(self):
        api = (WEB / "api.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/settings", response_class=HTMLResponse)', api)
        html = render("settings.html", "/settings")
        for key in ("camera", "notify", "cats", "ai", "remote", "computer"):
            self.assertIn(f'id="set-{key}"', html)
        self.assertIn("/static/js/settings-overview.js", html)
        base = render("base.html", "/settings")
        self.assertIn('href="/settings" title="設定" class="nav-primary active"', base)

    def test_ai_form_keeps_every_field_inside_one_form(self):
        html = render("settings.html", "/settings/ai")
        form = html[html.index('<form id="ai-settings-form"'):html.index("</form>", html.index('<form id="ai-settings-form"'))]
        fields = re.findall(r'id="(ai-[a-z0-9-]+)"', (TEMPLATES / "settings.html").read_text(encoding="utf-8"))
        js = (STATIC / "js" / "ai.js").read_text(encoding="utf-8")
        used = set(re.findall(r'"(ai-[a-z0-9-]+)"', js))
        for field in used & set(fields):
            if field.startswith(("ai-adaptive", "ai-runtime", "ai-clip", "ai-message")):
                continue
            self.assertIn(f'id="{field}"', form, field)
        self.assertIn('id="ai-clip-bar"', form)
        self.assertIn("settings-savebar", form)

    def test_sub_pages_link_back_to_overview(self):
        self.assertIn('href="/settings">← 設定', render("settings.html", "/settings/telegram"))
        self.assertIn('href="/settings">← 設定', render("diagnostics.html", "/diagnostics"))
        self.assertNotIn("← 設定", render("settings.html", "/settings/pets"))


class PhoneLayoutTests(unittest.TestCase):
    def test_bottom_tabs_replace_sidebar_on_phones(self):
        html = render("base.html", "/events")
        nav = html[html.index('<nav class="bottom-tabs"'):html.index("</nav>", html.index('<nav class="bottom-tabs"'))]
        self.assertEqual(re.findall(r"<span>([^<]+)</span></a>", nav), ["首頁", "即時", "事件", "貓咪", "設定"])
        self.assertEqual(nav.count('aria-current="page"'), 1)
        self.assertLess(html.index('<nav class="bottom-tabs"'), html.index("</body>"))
        self.assertNotIn("bottom-tabs", html[html.index('<dialog id="home-guide"'):html.index("</dialog>")])
        css = (STATIC / "css" / "warm.css").read_text(encoding="utf-8")
        phone = css[css.index("/* ===== 手機版 (r122)"):]
        self.assertIn(".sidebar{display:none!important}", phone)
        self.assertIn(".bottom-tabs{position:fixed", phone)

    def test_unique_ids_on_new_pages(self):
        for name, path in (("live.html", "/live"), ("events.html", "/events"), ("settings.html", "/settings"),
                           ("settings.html", "/settings/ai")):
            html = render(name, path)
            found = re.findall(r'\bid="([^"]+)"', html)
            self.assertEqual(len(found), len(set(found)), path)


if __name__ == "__main__":
    unittest.main()
