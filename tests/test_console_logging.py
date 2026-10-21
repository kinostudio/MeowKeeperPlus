import logging
import unittest

from meowkeeper.app.console_logging import ChineseAccessFormatter, QuietPreviewFilter


class ConsoleLoggingTests(unittest.TestCase):
    def record(self, path, code):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
                                 ("secret-client", "GET", path, "1.1", code), None)

    def test_polling_is_quiet_but_errors_remain_visible(self):
        filter_ = QuietPreviewFilter()
        self.assertFalse(filter_.filter(self.record("/api/v1/preview/jpeg?token=secret", 200)))
        self.assertTrue(filter_.filter(self.record("/api/v1/preview/jpeg?token=secret", 503)))

    def test_chinese_errors_and_no_request_secrets(self):
        formatter = ChineseAccessFormatter()
        for path in ("/api/v1/pets/private-id?token=secret", "/bot-secret/sensitive"):
            text = formatter.format(self.record(path, 401))
            self.assertIn("尚未登入或授權已到期", text)
            self.assertTrue(text.encode("cp950"))
            for forbidden in ("secret", "private-id", "sensitive", "token=", "secret-client"):
                self.assertNotIn(forbidden, text)
