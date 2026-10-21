import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TelegramUiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = (ROOT / "src/meowkeeper/app/web/templates/settings.html").read_text(encoding="utf-8")
        self.script = (ROOT / "src/meowkeeper/app/web/static/js/telegram.js").read_text(encoding="utf-8")
        self.styles = (ROOT / "src/meowkeeper/app/web/static/style.css").read_text(encoding="utf-8")

    def test_recipient_editor_has_visible_fallback_inputs(self) -> None:
        self.assertIn('id="telegram-recipient-list"', self.template)
        self.assertIn('placeholder="例如家人手機"', self.template)
        self.assertIn('placeholder="先傳 /start，再填入 Chat ID"', self.template)
        self.assertIn('id="telegram-recipient-selection"', self.template)

    def test_recipient_editor_supports_add_remove_and_selection(self) -> None:
        self.assertIn('id="add-telegram-recipient"', self.template)
        self.assertIn('id="save-telegram-recipients"', self.template)
        self.assertIn('/static/js/telegram.js?v=recipient-editor-20260926', self.template)
        self.assertIn('updateSelectionSummary', self.script)
        self.assertIn('data-remove-fallback', self.script)
        self.assertIn('Promise.allSettled', self.script)

    def test_recipient_rows_span_the_editor_width(self) -> None:
        self.assertIn(".telegram-recipient-row { grid-column: 1 / -1;", self.styles)


if __name__ == "__main__":
    unittest.main()
