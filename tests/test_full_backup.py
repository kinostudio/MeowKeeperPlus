"""Full encrypted ZIP backup covers application data without leaking secrets."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from meowkeeper.security.full_backup import FullBackupError, create_full_backup, restore_full_backup
from meowkeeper.storage.database import SQLiteStore


class _TelegramStore:
    def __init__(self) -> None:
        self.values = {
            "bot_token": "12345:TOP-SECRET-EXAMPLE",
            "chat_ids": "123456",
            "recipients": [{"alias": "主要手機", "chat_id": "123456"}],
        }

    def read_telegram(self):
        class Credentials:
            bot_token = self.values["bot_token"]
            chat_ids = self.values["chat_ids"]
            recipients_json = json.dumps(self.values["recipients"], ensure_ascii=False)

        return Credentials()

    def save_telegram(self, token, chat_ids, recipients=None):
        self.values = {"bot_token": token, "chat_ids": chat_ids, "recipients": recipients}

    def clear_telegram(self):
        self.values = {"bot_token": "", "chat_ids": "", "recipients": None}


class FullBackupTests(unittest.TestCase):
    def test_web_full_backup_export_and_import(self) -> None:
        from fastapi.testclient import TestClient

        from meowkeeper.app.web.api import create_app
        from meowkeeper.app.web.state import CoreStateFacade

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            config = root / "config.json"
            config.write_text('{"analysis_fps":5}', encoding="utf-8")
            store = SQLiteStore(data / "meowkeeper.db")
            (data / "pets" / "saki").mkdir(parents=True)
            (data / "pets" / "saki" / "profile.json").write_text("{}", encoding="utf-8")
            credentials = _TelegramStore()
            app = create_app(
                token="a" * 32,
                facade=CoreStateFacade(store=store, event_directory=data / "events"),
                source_config=SimpleNamespace(path=config),
                telegram_credentials=credentials,
            )
            headers = {
                "Authorization": "Bearer " + "a" * 32,
                "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token,
            }
            with TestClient(app) as client:
                page = client.get("/settings/advanced", headers=headers)
                self.assertEqual(page.status_code, 200)
                self.assertIn('id="full-backup-progress-region"', page.text)
                self.assertIn("settings.js?v=3", page.text)
                response = client.post(
                    "/api/v1/settings/backup/full-export",
                    headers=headers,
                    json={"password": "sixsix"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "queued")
                export_job_id = response.json()["job_id"]
                export_status = client.get(
                    f"/api/v1/settings/backup/jobs/{export_job_id}", headers=headers,
                )
                self.assertEqual(export_status.status_code, 200)
                self.assertEqual(export_status.json()["status"], "completed")
                self.assertEqual(export_status.json()["percent"], 100)
                self.assertTrue(export_status.json()["items"])
                self.assertNotIn(str(root), json.dumps(export_status.json(), ensure_ascii=False))
                downloaded = client.get(
                    f"/api/v1/settings/backup/jobs/{export_job_id}/download", headers=headers,
                )
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(downloaded.headers["content-type"], "application/zip")
                backup = root / "full.zip"
                backup.write_bytes(downloaded.content)
                config.write_text('{"analysis_fps":1}', encoding="utf-8")
                imported = client.post(
                    "/api/v1/settings/backup/full-import",
                    headers=headers,
                    data={"password": "sixsix", "confirm": "true"},
                    files={"file": ("full.zip", backup.read_bytes(), "application/zip")},
                )
                self.assertEqual(imported.status_code, 200)
                import_job_id = imported.json()["job_id"]
                import_status = client.get(
                    f"/api/v1/settings/backup/jobs/{import_job_id}", headers=headers,
                )
                self.assertEqual(import_status.status_code, 200)
                self.assertEqual(import_status.json()["status"], "completed")
                self.assertEqual(import_status.json()["percent"], 100)
                self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["analysis_fps"], 5)
                failed_import = client.post(
                    "/api/v1/settings/backup/full-import",
                    headers=headers,
                    data={"password": "wrong1", "confirm": "true"},
                    files={"file": ("full.zip", backup.read_bytes(), "application/zip")},
                )
                self.assertEqual(failed_import.status_code, 200)
                failed_status = client.get(
                    f"/api/v1/settings/backup/jobs/{failed_import.json()['job_id']}", headers=headers,
                ).json()
                self.assertEqual(failed_status["status"], "failed")
                self.assertIn("密碼錯誤", failed_status["error_reason_zh_tw"])
                self.assertEqual(failed_status["failed_during_zh_tw"], "解密並驗證密碼")
                self.assertNotIn(str(root), json.dumps(failed_status, ensure_ascii=False))
                corrupt_import = client.post(
                    "/api/v1/settings/backup/full-import",
                    headers=headers,
                    data={"password": "sixsix", "confirm": "true"},
                    files={"file": ("damaged.zip", b"not a zip archive", "application/zip")},
                )
                self.assertEqual(corrupt_import.status_code, 200)
                corrupt_status = client.get(
                    f"/api/v1/settings/backup/jobs/{corrupt_import.json()['job_id']}", headers=headers,
                ).json()
                self.assertEqual(corrupt_status["status"], "failed")
                self.assertIn("ZIP 檔案不完整", corrupt_status["error_reason_zh_tw"])
                self.assertEqual(corrupt_status["failed_during_zh_tw"], "讀取 ZIP 內容")
            store.close()

    def test_full_backup_roundtrip_and_secret_is_encrypted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            database = data / "meowkeeper.db"
            config = root / "config.json"
            config.write_text('{"analysis_fps":5}', encoding="utf-8")
            (data / "state").mkdir(parents=True)
            (data / "state" / "telegram-cat-notifications.json").write_text(
                '{"version":1,"recipients":{}}', encoding="utf-8"
            )
            (data / "pets" / "saki" / "reference_images").mkdir(parents=True)
            (data / "pets" / "saki" / "profile.json").write_text('{"display_name":"Saki"}', encoding="utf-8")
            (data / "pets" / "saki" / "reference_images" / "one.jpg").write_bytes(b"jpeg")
            (data / "pets" / "saki" / "embeddings.npz").write_bytes(b"embedding")
            (data / "events").mkdir(parents=True)
            (data / "events" / "event.mp4").write_bytes(b"event")
            (data / "cache" / "ring").mkdir(parents=True)
            (data / "cache" / "ring" / "volatile.ts").write_bytes(b"skip")

            store = SQLiteStore(database)
            telegram = _TelegramStore()
            output, info = create_full_backup(
                config_path=config,
                data_root=data,
                database_path=database,
                password="sixsix",
                telegram={
                    "bot_token": telegram.values["bot_token"],
                    "chat_ids": telegram.values["chat_ids"],
                    "recipients": telegram.values["recipients"],
                },
                database_connection=store.connection,
                database_lock=store._connection_lock,
                output_directory=root / "out",
            )
            self.assertGreater(info["file_count"], 4)
            self.assertNotIn(b"TOP-SECRET", output.read_bytes())
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {"manifest.json", "payload.bin"})
                manifest = json.loads(archive.read("manifest.json"))
                self.assertTrue(manifest["includes_secrets"])
                self.assertNotIn("data/cache/ring/volatile.ts", {item["path"] for item in manifest["files"]})

            config.write_text('{"analysis_fps":1}', encoding="utf-8")
            (data / "pets" / "saki" / "profile.json").write_text('{"display_name":"changed"}', encoding="utf-8")
            telegram.values["bot_token"] = "another-token"
            result = restore_full_backup(
                backup_path=output,
                password="sixsix",
                config_path=config,
                data_root=data,
                database_path=database,
                telegram_store=telegram,
                database_connection=store.connection,
                database_lock=store._connection_lock,
            )
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["analysis_fps"], 5)
            self.assertEqual(json.loads((data / "pets" / "saki" / "profile.json").read_text(encoding="utf-8"))["display_name"], "Saki")
            self.assertEqual(telegram.values["bot_token"], "12345:TOP-SECRET-EXAMPLE")
            self.assertGreaterEqual(result["restored_files"], 4)
            store.close()

    def test_backup_progress_reports_safe_item_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            (data / "pets" / "private-cat-name").mkdir(parents=True)
            (data / "pets" / "private-cat-name" / "photo-identifier.jpg").write_bytes(b"photo")
            config = root / "private-config.json"
            config.write_text("{}", encoding="utf-8")
            progress_events = []
            output, _info = create_full_backup(
                config_path=config,
                data_root=data,
                database_path=data / "meowkeeper.db",
                password="sixsix",
                output_directory=root / "out",
                on_progress=progress_events.append,
            )
            self.assertTrue(output.is_file())
            completed_items = [event for event in progress_events if event["item_complete"]]
            self.assertTrue(completed_items)
            self.assertIn("寵物登錄與參考照片", {event["item_label"] for event in completed_items})
            self.assertNotIn("private-cat-name", json.dumps(progress_events, ensure_ascii=False))
            self.assertNotIn("photo-identifier.jpg", json.dumps(progress_events, ensure_ascii=False))

    def test_wrong_password_does_not_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text('{"value":1}', encoding="utf-8")
            output, _ = create_full_backup(
                config_path=config,
                data_root=root / "data",
                database_path=root / "data" / "meowkeeper.db",
                password="sixsix",
                output_directory=root / "out",
            )
            with self.assertRaisesRegex(FullBackupError, "密碼錯誤"):
                restore_full_backup(
                    backup_path=output,
                    password="wrong1",
                    config_path=config,
                    data_root=root / "data",
                    database_path=root / "data" / "meowkeeper.db",
                )
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["value"], 1)


if __name__ == "__main__":
    unittest.main()
