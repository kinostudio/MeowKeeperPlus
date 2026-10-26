"""A shared document must retain one media session without duplicate page controls."""
import shutil
import subprocess
import unittest
from collections import Counter

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.preview_mse import validate_codec_offer
from meowkeeper.app.web.session import SessionManager
from tests.test_dashboard_experience import ROOT, TEMPLATES, Elements


class MonitorNavigationTests(unittest.TestCase):
    def test_routes_render_both_views_once_with_unique_targets(self):
        env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
        for path in ("/", "/dashboard", "/live"):
            with self.subTest(path=path):
                html = env.get_template("monitor.html").render(page_path=path, page_title="驗收")
                parsed = Elements()
                parsed.feed(html)
                self.assertFalse([key for key, count in Counter(parsed.ids).items() if count > 1])
                self.assertIn("overview-frame", parsed.ids)
                self.assertIn("preview-image", parsed.ids)
                self.assertEqual(parsed.ids.count("capture-result"), 1)
                for name in ("shared-preview.js", "monitor-navigation.js", "dashboard.js"):
                    self.assertEqual(sum(name + "?" in src for src in parsed.scripts), 1)
                hidden = 'data-monitor-view="overview" hidden inert' if path == "/live" else 'data-monitor-view="live" hidden inert'
                self.assertIn(hidden, html)

    def test_http_routes_use_the_shared_shell_and_preserve_auth(self):
        with TestClient(create_app(token="isolated-test-token-00000000000000")) as client:
            for path in ("/", "/dashboard", "/live"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('data-monitor-view="overview"', response.text)
                self.assertIn('data-monitor-view="live"', response.text)
        sessions = SessionManager()
        sessions.configure_admin("fixture", "fixture-only-password-123")
        with TestClient(create_app(session_manager=sessions)) as client:
            for path in ("/", "/dashboard", "/live"):
                self.assertEqual(client.get(path, follow_redirects=False).headers["location"], "/login")
            self.assertEqual(client.post("/api/v1/auth/login", json={"username":"fixture", "password":"fixture-only-password-123"}).status_code, 200)
            self.assertIn('data-monitor-view="live"', client.get("/live").text)

    def test_audio_offers_are_allowlisted(self):
        self.assertEqual(validate_codec_offer("hvc1.1.6.L153.B0,mp4a.40.2,opus"), "hvc1.1.6.L153.B0,mp4a.40.2,opus")
        for value in ("avc1.640029,private", "opus,opus", "aac?src=other"):
            with self.assertRaises(ValueError):
                validate_codec_offer(value)

    def test_shared_session_navigation_runtime(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "需要 JavaScript runtime 驗收切頁生命週期")
        result = subprocess.run([node, str(ROOT / "tests/js/monitor_navigation_runtime.cjs")],
                                capture_output=True, text=True, timeout=20, shell=False, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("passed: persistent monitor navigation", result.stdout)
