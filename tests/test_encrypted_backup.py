"""A backup must remain portable while its token is never plaintext in the file."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from meowkeeper.security.credentials import TelegramCredentials
from meowkeeper.security.encrypted_backup import decrypt_settings, encrypt_settings


class EncryptedBackupTests(unittest.TestCase):
    def test_roundtrip_without_plaintext_secret(self) -> None:
        payload = {
            "config": {"media": {"source_type": "mock"}, "analysis_fps": 5},
            "telegram": {"bot_token": "12345:TOP-SECRET-EXAMPLE", "chat_ids": "123456"},
        }
        backup = encrypt_settings(payload, "a-long-backup-password")
        self.assertNotIn("TOP-SECRET", json.dumps(backup))
        self.assertEqual(decrypt_settings(backup, "a-long-backup-password"), payload)

    def test_wrong_password_or_tampering_rejected(self) -> None:
        backup = encrypt_settings({"config": {}, "telegram": {}}, "a-long-backup-password")
        with self.assertRaisesRegex(ValueError, "未匯入任何設定"):
            decrypt_settings(backup, "a-different-password")
        backup["data"] = "!!!"
        with self.assertRaisesRegex(ValueError, "未匯入任何設定"):
            decrypt_settings(backup, "a-long-backup-password")

    def test_short_password_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少 6"):
            encrypt_settings({}, "short")

    def test_web_export_import_requires_auth_and_restores_credentials(self) -> None:
        from fastapi.testclient import TestClient

        from meowkeeper.app.web.api import create_app

        class FakeStore:
            values = TelegramCredentials("12345:TOP-SECRET-EXAMPLE", "123456")

            def read_telegram(self):
                return self.values

            def save_telegram(self, token, chat_ids, recipients=None):
                self.values = TelegramCredentials(token, chat_ids, json.dumps(recipients) if recipients else "")

            def clear_telegram(self):
                self.values = TelegramCredentials()

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text('{"media":{"source_type":"mock"},"analysis_fps":5}', encoding="utf-8")
            store = FakeStore()
            app = create_app(token="a" * 32, source_config=SimpleNamespace(path=path), telegram_credentials=store)
            headers = {"Authorization": "Bearer " + "a" * 32, "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token}
            with TestClient(app, client=("127.0.0.1", 12345)) as client:
                self.assertEqual(client.post("/api/v1/settings/backup/export", json={"password": "a-long-backup-password"}).status_code, 401)
                response = client.post("/api/v1/settings/backup/export", headers=headers, json={"password": "a-long-backup-password"})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("TOP-SECRET", response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
                backup = response.json()
                path.write_text('{"media":{"source_type":"mock"},"analysis_fps":1}', encoding="utf-8")
                store.values = TelegramCredentials("another-token", "9999")
                wrong = client.post("/api/v1/settings/backup/import", headers=headers, json={"password": "wrong-password-123", "backup": backup, "confirm": True})
                self.assertEqual(wrong.status_code, 422)
                self.assertEqual(store.values.bot_token, "another-token")
                restored = client.post("/api/v1/settings/backup/import", headers=headers, json={"password": "a-long-backup-password", "backup": backup, "confirm": True})
                self.assertEqual(restored.status_code, 200)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["analysis_fps"], 5)
                self.assertEqual(store.values.bot_token, "12345:TOP-SECRET-EXAMPLE")


if __name__ == "__main__":
    unittest.main()
