import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from meowkeeper.app.web import create_app
from meowkeeper.app.web.telemetry_help import load_help_terms


class TelemetryHelpTests(unittest.TestCase):
    def test_shared_dictionary_is_complete_and_plain_language(self):
        terms = load_help_terms()
        self.assertEqual(len(terms), 36)
        for key in ("ai_grace", "telegram_accepted", "parallel", "queue_wait", "p95_latency"):
            self.assertIn(key, terms)
        self.assertIn("不等於", terms["telegram_accepted"]["important_note"])
        self.assertIn("0～10", terms["ai_grace"]["why_or_next_step"])

    def test_real_templates_use_shared_accessible_help_without_new_requests(self):
        token = "isolated-help-test-token"
        with TestClient(create_app(token=token)) as client:
            for page in ("/analytics", "/events"):
                response = client.get(page, headers={"Authorization": "Bearer " + token})
                self.assertEqual(response.status_code, 200)
                self.assertIn('data-help-term="tag_review"', response.text)
                self.assertIn('aria-label="說明：確認 AI 看得對不對"', response.text)
                self.assertIn("/static/js/term-help.js", response.text)
                self.assertIn("/static/css/term-help.css", response.text)
        root = Path(__file__).resolve().parents[1] / "src/meowkeeper/app/web/static"
        script = (root / "js/term-help.js").read_text(encoding="utf-8")
        self.assertIn('event.key !== "Escape"', script)
        self.assertNotIn("fetch(", script)
        self.assertNotIn("innerHTML", script)


if __name__ == "__main__":
    unittest.main()
