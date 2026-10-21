import os
import tempfile
import unittest
from unittest.mock import patch

from meowkeeper.core.notifications.telegram import TelegramNotifier
from tools import launcher, run_host


class HostTelegramWiringTests(unittest.TestCase):
    def test_saved_empty_or_disabled_recipients_never_enable_legacy_ids(self) -> None:
        from meowkeeper.security.credentials import TelegramCredentials

        args = run_host.build_parser().parse_args(["--enable-telegram"])
        for saved in ('[]', 'invalid', '[{"alias":"手機","chat_id":"123","enabled":false}]'):
            with self.subTest(saved=saved):
                credentials = TelegramCredentials("stored-token", "789", saved)
                self.assertEqual(credentials.selected_chat_ids, ())

                class Store:
                    def __init__(self, value):
                        self.value = value

                    def read_telegram(self):
                        return self.value

                with patch.dict(os.environ, {}, clear=True), patch.object(run_host, "TelegramNotifier") as notifier:
                    self.assertEqual(run_host._telegram_options(args, Store(credentials)), (False, None, ()))
                    notifier.assert_not_called()

    def test_notifications_are_disabled_without_explicit_flag(self) -> None:
        args = run_host.build_parser().parse_args([])
        with tempfile.TemporaryDirectory(prefix="meowkeeper-telegram-default-") as directory:
            args.dashboard_config = os.path.join(directory, "config.json")
            with patch.dict(
                os.environ,
                {
                    "MEOWKEEPER_TELEGRAM_BOT_TOKEN": "would-not-send",
                    "MEOWKEEPER_TELEGRAM_CHAT_IDS": "123,456",
                },
                clear=False,
            ):
                enabled, notifier, chat_ids = run_host._telegram_options(args)
        self.assertFalse(enabled)
        self.assertIsNone(notifier)
        self.assertEqual(chat_ids, ())

    def test_enabled_notifications_require_both_environment_values(self) -> None:
        args = run_host.build_parser().parse_args(["--enable-telegram"])
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "Bot Token"):
            run_host._telegram_options(args)

    def test_enabled_notifications_deduplicate_chat_ids(self) -> None:
        args = run_host.build_parser().parse_args(["--enable-telegram"])
        with patch.dict(
            os.environ,
            {
                "MEOWKEEPER_TELEGRAM_BOT_TOKEN": "test-token",
                "MEOWKEEPER_TELEGRAM_CHAT_IDS": "123, 123,,456",
            },
            clear=True,
        ):
            enabled, notifier, chat_ids = run_host._telegram_options(args)
        self.assertTrue(enabled)
        self.assertIsInstance(notifier, TelegramNotifier)
        self.assertEqual(chat_ids, ("123", "456"))

    def test_enabled_notifications_can_read_local_credential_store(self) -> None:
        args = run_host.build_parser().parse_args(["--enable-telegram"])

        class Store:
            def read_telegram(self):
                return type("Credentials", (), {"bot_token": "stored-token", "chat_ids": "789"})()

        with patch.dict(os.environ, {}, clear=True):
            enabled, notifier, chat_ids = run_host._telegram_options(args, Store())
        self.assertTrue(enabled)
        self.assertIsInstance(notifier, TelegramNotifier)
        self.assertEqual(chat_ids, ("789",))

    def test_enabled_notifications_prefer_selected_named_recipients(self) -> None:
        args = run_host.build_parser().parse_args(["--enable-telegram"])

        class Store:
            def read_telegram(self):
                return type(
                    "Credentials",
                    (),
                    {
                        "bot_token": "stored-token",
                        "chat_ids": "legacy",
                        "selected_chat_ids": ("123", "456"),
                    },
                )()

        with patch.dict(os.environ, {}, clear=True):
            enabled, notifier, chat_ids = run_host._telegram_options(args, Store())
        self.assertTrue(enabled)
        self.assertIsInstance(notifier, TelegramNotifier)
        self.assertEqual(chat_ids, ("123", "456"))

    def test_launcher_exposes_explicit_telegram_flag(self) -> None:
        args = launcher.build_parser().parse_args(["--enable-telegram"])
        self.assertTrue(args.enable_telegram)
        self.assertEqual(args.telegram_token_env, "MEOWKEEPER_TELEGRAM_BOT_TOKEN")
        self.assertEqual(args.telegram_chat_ids_env, "MEOWKEEPER_TELEGRAM_CHAT_IDS")


if __name__ == "__main__":
    unittest.main()
