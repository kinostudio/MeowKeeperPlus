from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from meowkeeper.app.source_config import (
    SourceConfig,
    SourceConfigService,
    load_runtime_settings,
    load_source_config,
    save_runtime_settings,
)
from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.state import CoreStateFacade


class Phase3SourceMvpTests(unittest.TestCase):
    def test_analyzer_mode_validation_and_safe_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertEqual(load_runtime_settings(path)["analyzer_mode"], "yolo")
            save_runtime_settings(path, {"analyzer_mode": "v02"})
            self.assertEqual(load_runtime_settings(path)["analyzer_mode"], "v02")
            for invalid in ("noop", "auto", "unknown", None, True, []):
                with self.assertRaises(ValueError):
                    save_runtime_settings(path, {"analyzer_mode": invalid})
            self.assertEqual(load_runtime_settings(path)["analyzer_mode"], "v02")
            path.write_text('{"dashboard_settings":{"analyzer_mode":"invalid"}}', encoding="utf-8")
            self.assertEqual(load_runtime_settings(path)["analyzer_mode"], "yolo")

    def test_dashboard_runtime_settings_persist_without_touching_source_or_secrets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-runtime-settings-") as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({
                    "schema_version": 3,
                    "media": {"source_type": "youtube", "youtube": {"url": "https://www.youtube.com/watch?v=public-test"}},
                    "telegram": {"enabled": True, "bot_secret_id": "telegram_primary"},
                }),
                encoding="utf-8",
            )
            save_runtime_settings(path, {"confidence_threshold": 0.61, "analysis_fps": 3, "activity_fps": 2})
            loaded = load_runtime_settings(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["confidence_threshold"], 0.61)
            self.assertEqual(loaded["analysis_fps"], 3)
            self.assertEqual(loaded["activity_fps"], 2)
            self.assertEqual(load_source_config(path).source_type, "youtube")
            self.assertEqual(payload["telegram"]["bot_secret_id"], "telegram_primary")
            self.assertNotIn("token", json.dumps(payload).lower())

    def test_ai_settings_api_round_trip_uses_persistence_callback(self) -> None:
        token = "a" * 32
        csrf = hashlib.sha256((token + ":csrf").encode()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="meowkeeper-ai-api-") as directory:
            path = Path(directory) / "config.json"
            persisted: list[dict[str, object]] = []

            def persist(values: dict[str, object]) -> dict[str, object]:
                persisted.append(dict(values))
                save_runtime_settings(path, values)
                return {"status": "accepted", "restart_required": True}

            facade = CoreStateFacade(callbacks={"settings": persist})
            app = create_app(facade=facade, token=token)
            headers = {"Authorization": "Bearer " + token, "X-MeowKeeper-CSRF": csrf}
            with TestClient(app) as client:
                page = client.get("/settings/ai", headers=headers)
                self.assertEqual(page.status_code, 200)
                self.assertIn('id="ai-identity-frame-width"', page.text)
                self.assertIn('id="ai-identity-frame-width-help"', page.text)
                response = client.put(
                    "/api/v1/settings",
                    headers=headers,
                    json={
                        "analyzer_mode": "v02",
                        "confidence_threshold": 0.57,
                        "analysis_fps": 4,
                        "activity_fps": 2,
                        "identity_frame_width": 960,
                        "cooldown_seconds": 90,
                        "event_pre_seconds": 4,
                        "event_post_seconds": 8,
                        "retention_seconds": 60,
                        "ring_maximum_size_mb": 256,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["restart_required"])
                self.assertEqual(load_runtime_settings(path)["analyzer_mode"], "v02")
                self.assertEqual(load_runtime_settings(path)["identity_frame_width"], 960)
                self.assertEqual(client.put("/api/v1/settings", headers=headers,
                                            json={"analyzer_mode": "noop"}).status_code, 422)
                self.assertEqual(persisted[0]["confidence_threshold"], 0.57)
                self.assertEqual(load_runtime_settings(path)["analysis_fps"], 4)
                reset = client.post("/api/v1/settings/reset", headers=headers, json={"reauth": True})
                self.assertEqual(reset.status_code, 200)
                self.assertEqual(load_runtime_settings(path)["analyzer_mode"], "yolo")
                self.assertEqual(load_runtime_settings(path)["ring_maximum_size_mb"], 512.0)

    def test_source_config_persists_page_url_not_signed_media_url(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-source-") as directory:
            path = Path(directory) / "config.json"
            config = SourceConfig("youtube", "https://www.youtube.com/watch?v=public-test", 720)
            service = SourceConfigService(path)
            service.save(config)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(load_source_config(path), config)
            self.assertEqual(payload["media"]["youtube"]["url"], config.url)
            self.assertNotIn("googlevideo", path.read_text(encoding="utf-8"))

    def test_web_source_update_reaches_core_callback_and_cameras_alias(self) -> None:
        token = "s" * 32
        csrf = hashlib.sha256((token + ":csrf").encode()).hexdigest()
        configured: list[str | None] = []
        with tempfile.TemporaryDirectory(prefix="meowkeeper-source-api-") as directory:
            service = SourceConfigService(Path(directory) / "config.json")
            started_with: list[str | None] = []
            current_source: dict[str, str | None] = {"url": None}

            def configure_source(value: str | None) -> dict[str, str]:
                configured.append(value)
                current_source["url"] = value
                return {"status": "accepted"}

            def start_monitor() -> dict[str, str]:
                started_with.append(current_source["url"])
                return {"status": "starting"}

            facade = CoreStateFacade(callbacks={"source_config": configure_source, "start": start_monitor})
            app = create_app(facade=facade, token=token, source_config=service)
            headers = {"Authorization": "Bearer " + token, "X-MeowKeeper-CSRF": csrf}
            with TestClient(app) as client:
                response = client.put(
                    "/api/v1/source/config",
                    headers=headers,
                    json={
                        "source_type": "rtsp",
                        "url": "rtsp://192.0.2.10:554/live",
                        "max_height": 720,
                        "reauth": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(configured, ["rtsp://192.0.2.10:554/live"])
                start_response = client.post("/api/v1/monitor/start", headers=headers, json={})
                self.assertEqual(start_response.status_code, 200)
                self.assertEqual(started_with, ["rtsp://192.0.2.10:554/live"])
                self.assertEqual(client.get("/api/v1/source/config", headers=headers).json()["source"]["source_type"], "rtsp")
                self.assertEqual(client.get("/cameras", follow_redirects=False).status_code, 307)
                self.assertEqual(client.get("/cameras", follow_redirects=False).headers["location"], "/settings/source")
                self.assertEqual(client.get("/settings/source", headers=headers).status_code, 200)

    def test_web_rejects_rtsp_userinfo_and_signed_url(self) -> None:
        with self.assertRaises(ValueError):
            SourceConfig("rtsp", "rtsp://admin:secret@192.0.2.10/live").validate()
        with self.assertRaises(ValueError):
            SourceConfig("youtube", "https://googlevideo.example/signed").validate()

    def test_host_restart_requires_confirmation_and_uses_runtime_callback(self) -> None:
        token = "r" * 32
        csrf = hashlib.sha256((token + ":csrf").encode()).hexdigest()
        calls: list[str] = []
        app = create_app(
            facade=CoreStateFacade(),
            token=token,
            host_restart=lambda: calls.append("restart") or {"status": "restarting"},
        )
        headers = {"Authorization": "Bearer " + token, "X-MeowKeeper-CSRF": csrf}
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/v1/host/restart", headers=headers, json={}).status_code, 428)
            response = client.post("/api/v1/host/restart", headers=headers, json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "restarting")
        self.assertEqual(calls, ["restart"])

    def test_telegram_settings_and_test_callback_are_reachable_without_exposing_secrets(self) -> None:
        token = "t" * 32
        csrf = hashlib.sha256((token + ":csrf").encode()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="meowkeeper-telegram-api-") as directory:
            service = SourceConfigService(Path(directory) / "config.json")
            received: list[dict[str, object]] = []

            class MemoryCredentials:
                def __init__(self) -> None:
                    self.token = ""
                    self.chat_ids = ""
                    self.recipients = []

                def status(self) -> dict[str, object]:
                    return {
                        "backend": "test_memory",
                        "bot_token_configured": bool(self.token),
                        "chat_ids_configured": bool(self.chat_ids),
                        "recipient_count": len(self.recipients),
                        "recipients": list(self.recipients),
                    }

                def save_telegram(self, bot_token: str, chat_ids: str, recipients=None) -> None:
                    self.token = bot_token
                    self.chat_ids = chat_ids
                    self.recipients = list(recipients or [])

                def save_recipients(self, recipients) -> None:
                    self.recipients = list(recipients)

                def clear_telegram(self) -> None:
                    self.token = ""
                    self.chat_ids = ""

            credentials = MemoryCredentials()

            def telegram_test(payload: dict[str, object]) -> dict[str, object]:
                received.append(payload)
                return {"status": "disabled", "sent": 0}

            app = create_app(
                facade=CoreStateFacade(callbacks={"telegram_test": telegram_test}),
                token=token,
                source_config=service,
                telegram_credentials=credentials,
            )
            headers = {"Authorization": "Bearer " + token, "X-MeowKeeper-CSRF": csrf}
            with patch.dict(
                os.environ,
                {"MEOWKEEPER_TELEGRAM_BOT_TOKEN": "", "MEOWKEEPER_TELEGRAM_CHAT_IDS": ""},
                clear=False,
            ), TestClient(app) as client:
                self.assertEqual(client.get("/settings/telegram", headers=headers).status_code, 200)
                config = client.get("/api/v1/telegram/config", headers=headers).json()
                self.assertFalse(config["bot_token_configured"])
                self.assertFalse(config["chat_ids_configured"])
                page = client.get("/settings/telegram", headers=headers)
                self.assertIn("Bot Token 與 Chat ID 安全引導", page.text)
                self.assertIn("telegram-bot-token", page.text)
                save = client.post(
                    "/api/v1/telegram/credentials",
                    headers=headers,
                    json={"bot_token": "local-only-token", "chat_ids": "123,456", "reauth": True},
                )
                self.assertEqual(save.status_code, 200)
                self.assertTrue(save.json()["bot_token_configured"])
                self.assertTrue(save.json()["chat_ids_configured"])
                self.assertNotIn("local-only-token", json.dumps(save.json()))
                self.assertEqual(credentials.chat_ids, "123,456")
                recipients = client.post(
                    "/api/v1/telegram/recipients",
                    headers=headers,
                    json={
                        "recipients": [
                            {"alias": "主要手機", "chat_id": "123", "enabled": True},
                            {"alias": "家庭群組", "chat_id": "456", "enabled": False},
                        ],
                        "reauth": True,
                    },
                )
                self.assertEqual(recipients.status_code, 200)
                self.assertEqual(recipients.json()["recipient_count"], 2)
                response = client.post("/api/v1/telegram/test", headers=headers, json={})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(received, [{}])
                self.assertNotIn("local-only", json.dumps(config))
                clear = client.post(
                    "/api/v1/telegram/credentials",
                    headers=headers,
                    json={"clear": True, "reauth": True},
                )
                self.assertEqual(clear.status_code, 200)
                self.assertFalse(clear.json()["bot_token_configured"])

    def test_telegram_page_exposes_explicit_apply_and_restart_action(self) -> None:
        template = Path("src/meowkeeper/app/web/templates/settings.html").read_text(encoding="utf-8")
        script = Path("src/meowkeeper/app/web/static/js/telegram.js").read_text(encoding="utf-8")
        self.assertIn('id="apply-telegram"', template)
        self.assertIn("啟用 Telegram 並重新啟動 Host", template)
        self.assertIn('/api/v1/host/restart', script)
        self.assertIn('JSON.stringify({ confirm: true })', script)
        self.assertIn('JSON.stringify({ enabled: true, reauth: true })', script)


if __name__ == "__main__":
    unittest.main()
