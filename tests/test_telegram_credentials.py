from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from meowkeeper.security.credentials import TelegramCredentials, TelegramCredentialStore


@unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
class TelegramCredentialStoreTests(unittest.TestCase):
    def test_dpapi_round_trip_does_not_write_plaintext(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-dpapi-") as directory:
            path = Path(directory) / "telegram.dpapi"
            store = TelegramCredentialStore(fallback_path=path)
            store._use_dpapi = True  # force the tested fallback without touching real user credentials
            token = "000000:test-dpapi-token"
            store.save_telegram(
                token,
                "123,-100456",
                recipients=[
                    {"alias": "主要手機", "chat_id": "123", "enabled": True},
                    {"alias": "家庭群組", "chat_id": "-100456", "enabled": True},
                ],
            )
            self.assertTrue(path.is_file())
            self.assertNotIn(token.encode(), path.read_bytes())
            values = store.read_telegram()
            self.assertEqual(values.bot_token, token)
            self.assertEqual(values.chat_ids, "123,-100456")
            self.assertEqual([item.alias for item in values.recipients], ["主要手機", "家庭群組"])
            self.assertEqual(values.selected_chat_ids, ("123", "-100456"))
            store.save_recipients(
                [
                    {"recipient_id": values.recipients[0].recipient_id, "alias": "新手機名稱", "enabled": True},
                    {"recipient_id": values.recipients[1].recipient_id, "alias": "停用群組", "enabled": False},
                ]
            )
            updated = store.read_telegram()
            self.assertEqual(updated.recipients[0].alias, "新手機名稱")
            self.assertEqual(updated.selected_chat_ids, ("123",))
            store.clear_telegram()
            self.assertFalse(path.exists())

    def test_legacy_chat_ids_are_visible_as_masked_editor_rows(self) -> None:
        values = TelegramCredentials(bot_token="stored", chat_ids="123,-100456")
        self.assertEqual([item.alias for item in values.effective_recipients], ["通知對象 1", "通知對象 2"])
        self.assertEqual([item.recipient_id for item in values.effective_recipients], [
            TelegramCredentials(bot_token="", chat_ids="123").effective_recipients[0].recipient_id,
            TelegramCredentials(bot_token="", chat_ids="-100456").effective_recipients[0].recipient_id,
        ])


if __name__ == "__main__":
    unittest.main()
