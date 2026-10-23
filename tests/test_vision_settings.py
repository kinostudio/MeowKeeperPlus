import base64
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from meowkeeper.app.vision_settings import VisionSettingsStore
from meowkeeper.app.web import create_app
from meowkeeper.core.event_semantics import ProviderReply, SemanticRequest, VisionFailure
from meowkeeper.core.semantic_storyboard import TrackStoryboard
from meowkeeper.security.credentials import TelegramCredentialStore
from meowkeeper.security.full_backup import _iter_data_files
from meowkeeper.storage.database import SQLiteStore


class FakeProtector:
    def _dpapi(self, value, *, protect):
        return base64.b64encode(value) if protect else base64.b64decode(value)


class VisionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.store = VisionSettingsStore(self.root, protector=FakeProtector())

    def tearDown(self):
        self.folder.cleanup()

    def settings(self, **kwargs):
        values = {"endpoint": "https://example.invalid/v1/chat/completions", "model_id": "test-model",
                  "enabled": True, "confirm_upload": True}
        values.update(kwargs)
        return values

    def test_defaults_off_and_key_separate_from_config_and_backup(self):
        self.assertIsNone(self.store.build_provider())
        self.store.save(self.settings())
        key = "TEST_ONLY_KEY_NOT_REAL"
        self.store.save_key(key)
        self.assertEqual(self.store.read_key(), key)
        self.assertNotIn(key, self.store.path.read_text())
        self.assertNotIn(key, json.dumps(self.store.status()))
        self.assertTrue(self.store.status()["key_configured"])
        entries = _iter_data_files(self.root, self.root / "events.db")
        self.assertFalse(any("secrets/vision" in name for name, _path in entries))
        self.assertTrue(any(name.endswith("vision-analysis.json") for name, _path in entries))

    def test_change_destination_requires_new_consent_and_does_not_reuse_key(self):
        self.store.save(self.settings())
        self.store.save_key("TEST_ONLY_FIRST_KEY")
        original = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.save({"endpoint": "https://other.invalid/v1/chat/completions"})
        self.assertEqual(self.store.path.read_bytes(), original)
        self.store.save({"endpoint": "https://other.invalid/v1/chat/completions", "confirm_upload": True})
        self.assertFalse(self.store.status()["key_configured"])
        self.assertIsNone(self.store.read_key())

    def test_quota_and_storyboard_preferences_are_isolated_per_provider_model(self):
        self.store.save({"provider_id": "openai_compatible", "endpoint": "https://one.invalid/v1/chat/completions",
                         "model_id": "model-a"})
        # A boundary switch loads that model's saved profile (or safe defaults).
        # The user can then edit its profile without copying another model's limits.
        self.store.save({"daily_request_limit": 123, "storyboard_frame_count": 6,
                         "storyboard_jpeg_quality": 88})
        self.store.save({"endpoint": "https://two.invalid/v1/chat/completions", "model_id": "model-b"})
        self.assertEqual(self.store.read()["daily_request_limit"], 400)
        self.assertEqual(self.store.read()["storyboard_frame_count"], 4)
        self.store.save({"endpoint": "https://one.invalid/v1/chat/completions", "model_id": "model-a"})
        self.assertEqual(self.store.read()["daily_request_limit"], 123)
        self.assertEqual(self.store.read()["storyboard_frame_count"], 6)
        self.assertEqual(self.store.read()["storyboard_jpeg_quality"], 88)

    def test_disable_stops_new_request_without_restarting_host(self):
        self.store.save(self.settings())
        provider = self.store.build_provider()
        self.store.save({"enabled": False})
        with patch("meowkeeper.integrations.vision.openai_compatible.request_json") as send:
            with self.assertRaises(VisionFailure) as caught:
                provider.analyze(SemanticRequest("e", b"jpeg"), timeout_seconds=1)
            self.assertEqual(caught.exception.category, "skipped_disabled")
            send.assert_not_called()
        with self.assertRaises(ValueError):
            self.store.save({"enabled": True})

    def test_boundary_switch_preserves_explicit_profile_edits(self):
        saved = self.store.save({"provider_id": "gemini", "daily_request_limit": 73,
                                 "storyboard_jpeg_quality": 91, "storyboard_frame_count": 6,
                                 "storyboard_include_scene_references": True})["settings"]
        self.assertEqual(saved["daily_request_limit"], 73)
        self.assertEqual(saved["storyboard_jpeg_quality"], 91)
        self.assertEqual(saved["storyboard_frame_count"], 6)
        self.assertTrue(saved["storyboard_include_scene_references"])
        self.assertFalse(saved["enabled"])
        original = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.save({"model_id": "new-model", "daily_request_limit": 0})
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_returning_model_loads_profile_then_applies_explicit_edits(self):
        endpoint = "https://example.invalid/v1/chat/completions"
        self.store.save({"endpoint": endpoint, "model_id": "a", "daily_request_limit": 123,
                         "storyboard_jpeg_quality": 88})
        self.store.save({"model_id": "b", "daily_request_limit": 222})
        saved = self.store.save({"model_id": "a", "daily_request_limit": 66})["settings"]
        self.assertEqual(saved["daily_request_limit"], 66)
        self.assertEqual(saved["storyboard_jpeg_quality"], 88)
        self.assertEqual(self.store.save({"model_id": "b"})["settings"]["daily_request_limit"], 222)

    def test_disabled_partial_endpoint_draft_saves_but_cannot_enable(self):
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/test-image:generateContent"
        self.store.save({"provider_id": "gemini", "endpoint": endpoint, "model_id": "test-image"})
        saved = self.store.save({"provider_id": "openai_compatible", "model_id": "",
                                 "daily_request_limit": 87})["settings"]
        self.assertEqual(saved["endpoint"], endpoint)
        self.assertEqual(saved["daily_request_limit"], 87)
        self.assertIsNone(self.store.build_provider())
        with patch("meowkeeper.app.vision_settings.health_check") as check:
            self.assertEqual(self.store.check_connection()["status"], "unavailable")
            check.assert_not_called()
        original = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.save({"enabled": True, "confirm_upload": True})
        with self.assertRaises(ValueError):
            self.store.save({"endpoint": endpoint + "?key=TEST_ONLY_MUST_NOT_SAVE"})
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_save_api_distinguishes_storage_failure_without_exposing_exception(self):
        app = create_app(token="isolated-save-errors", vision_settings=self.store)
        headers = {"Authorization": "Bearer isolated-save-errors",
                   "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token}
        with TestClient(app) as client:
            with patch.object(self.store, "save", side_effect=PermissionError("PRIVATE_PATH_TEST_ONLY")):
                reply = client.put("/api/v1/vision/settings", json={}, headers=headers)
            self.assertEqual(reply.status_code, 503)
            self.assertNotIn("PRIVATE_PATH_TEST_ONLY", reply.text)
            reply = client.put("/api/v1/vision/settings", json={
                "endpoint": "https://[TEST_ONLY_SENSITIVE]/v1/chat/completions", "model_id": "test-model",
            }, headers=headers)
            self.assertEqual(reply.status_code, 422)
            self.assertNotIn("TEST_ONLY_SENSITIVE", reply.text)
            self.assertIn("網址格式", reply.json()["detail"])

    def test_invalid_fields_wait_and_corrupt_file_fail_closed(self):
        for value in (-1, 11, True, float("nan")):
            with self.assertRaises(ValueError):
                self.store.save({"grace_seconds": value})
        with self.assertRaises(ValueError):
            self.store.save({"api_key": "must-not-write"})
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text('{"secret":"must-not-return"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.build_provider()

    def test_old_settings_migrate_in_memory_without_rewriting_original(self):
        self.store.save(self.settings(enabled=False))
        old = self.store.read()
        for field in ("quota_profiles", *self.store.read().keys()):
            if field in {"schema_version", "provider_id", "endpoint", "model_id", "enabled",
                         "json_object_supported", "grace_seconds", "consent_reference", "consent_at"}:
                continue
            old.pop(field, None)
        old["schema_version"] = 1
        self.store.path.write_text(json.dumps(old), encoding="utf-8")
        original = self.store.path.read_bytes()
        values = self.store.read()
        self.assertEqual(values["daily_request_limit"], 100)
        self.assertEqual(values["schema_version"], 7)
        self.assertFalse(values["storyboard_include_scene_references"])
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_schema_four_migration_preserves_existing_quality_and_defaults_new_resolution(self):
        self.store.save({"storyboard_frame_count": 7, "storyboard_size": "2048x1365",
                         "storyboard_jpeg_quality": 91})
        values = self.store.read()
        values["schema_version"] = 4
        values.pop("storyboard_media_resolution")
        values.pop("event_merge_cooldown_seconds")
        for profile in values["quota_profiles"].values():
            profile.pop("storyboard_media_resolution")
            profile.pop("event_merge_cooldown_seconds")
        self.store.path.write_text(json.dumps(values), encoding="utf-8")
        migrated = self.store.read()
        self.assertEqual(migrated["schema_version"], 7)
        self.assertEqual(migrated["storyboard_frame_count"], 7)
        self.assertEqual(migrated["storyboard_size"], "2048x1365")
        self.assertEqual(migrated["storyboard_media_resolution"], "auto")
        self.assertEqual(migrated["event_merge_cooldown_seconds"], 0)

    def test_unpublished_legacy_resolution_values_are_reset_to_auto(self):
        self.store.save({"storyboard_frame_count": 6, "storyboard_size": "2048x1365"})
        values = self.store.read()
        values["schema_version"] = 5
        values["storyboard_image_detail"] = "high"
        for profile in values["quota_profiles"].values():
            profile["storyboard_image_detail"] = "high"
        self.store.path.write_text(json.dumps(values), encoding="utf-8")
        migrated = self.store.read()
        self.assertEqual(migrated["schema_version"], 7)
        self.assertEqual(migrated["storyboard_media_resolution"], "auto")
        self.assertNotIn("storyboard_image_detail", migrated)
        self.assertEqual(migrated["storyboard_frame_count"], 6)
        self.assertEqual(migrated["storyboard_size"], "2048x1365")

    def test_scene_reference_upload_is_opt_in_and_requires_separate_consent(self):
        base = {"provider_id": "openai_compatible", "endpoint": "https://example.invalid/v1/chat/completions",
                "model_id": "fixture", "enabled": True, "confirm_upload": True}
        self.store.save(base)
        with self.assertRaisesRegex(ValueError, "場景參考拼貼"):
            self.store.save({"storyboard_include_scene_references": True})
        saved = self.store.save({"storyboard_include_scene_references": True,
                                 "confirm_scene_reference_upload": True})
        self.assertTrue(saved["settings"]["storyboard_include_scene_references"])
        self.assertEqual(saved["settings"]["scene_reference_consent_reference"], self.store.boundary(saved["settings"]))
        self.assertTrue(self.store.build_provider().storyboard_options["include_scene_references"])
        saved = self.store.save({"storyboard_include_scene_references": False})
        self.assertFalse(saved["settings"]["storyboard_include_scene_references"])
        self.assertIsNone(saved["settings"]["scene_reference_consent_reference"])
        self.assertFalse(self.store.build_provider().storyboard_options["include_scene_references"])

    def test_schema_six_settings_migrate_scene_references_off(self):
        self.store.save({"endpoint": "https://example.invalid/v1/chat/completions", "model_id": "fixture"})
        legacy = self.store.read()
        legacy["schema_version"] = 6
        legacy.pop("storyboard_include_scene_references")
        legacy.pop("scene_reference_consent_reference")
        legacy.pop("scene_reference_consent_at")
        for profile in legacy["quota_profiles"].values():
            profile.pop("storyboard_include_scene_references")
        self.store.path.write_text(json.dumps(legacy), encoding="utf-8")
        migrated = self.store.read()
        self.assertEqual(migrated["schema_version"], 7)
        self.assertFalse(migrated["storyboard_include_scene_references"])
        self.assertFalse(next(iter(migrated["quota_profiles"].values()))["storyboard_include_scene_references"])

    def test_resolution_mapping_is_provider_specific(self):
        gemini = {**self.store.read(), "provider_id": "gemini", "storyboard_media_resolution": "medium"}
        openai = {**self.store.read(), "provider_id": "openai_compatible", "storyboard_media_resolution": "high"}
        self.assertEqual(self.store.effective_canvas_size(gemini), "1536x1024")
        self.assertEqual(self.store.provider_image_detail(gemini), "provider_default")
        self.assertEqual(self.store.provider_image_detail(openai), "high")
        openai["storyboard_media_resolution"] = "auto"
        self.assertEqual(self.store.provider_image_detail(openai), "provider_default")
        with self.assertRaises(ValueError):
            self.store.save({"storyboard_media_resolution": "medium"})

    def test_usage_endpoint_is_authenticated_and_returns_real_empty_state(self):
        database = SQLiteStore(self.root / "usage.db")
        manager = VisionSettingsStore(self.root / "usage-manager", protector=FakeProtector(), store=database)
        app = create_app(token="isolated-vision-usage", vision_settings=manager)
        headers = {"Authorization": "Bearer isolated-vision-usage"}
        try:
            with TestClient(app, base_url="https://testserver") as client:
                self.assertEqual(client.get("/api/v1/vision/usage").status_code, 401)
                self.assertEqual(client.get("/api/v1/vision/usage?hours=2", headers=headers).status_code, 422)
                self.assertEqual(client.get("/api/v1/vision/usage?limit=201", headers=headers).status_code, 422)
                response = client.get("/api/v1/vision/usage?hours=24", headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["requests"], 0)
                self.assertTrue(response.json()["buckets"])
                self.assertTrue(all(bucket["requests"] == 0 for bucket in response.json()["buckets"]))
                self.assertNotIn("TEST_ONLY_KEY", response.text)
                reference = manager.boundary(manager.read())
                for index in range(3):
                    event_id = f"pagination-{index}"
                    job = manager.guard.reserve(reference, SemanticRequest(event_id, b"synthetic"),
                                                daily_limit=20, model_id="test-model")
                    manager.guard.finish(job, "completed", request_dispatched=True, http_status=200)
                first = client.get("/api/v1/vision/usage?hours=24&limit=2&offset=0", headers=headers).json()
                second = client.get("/api/v1/vision/usage?hours=24&limit=2&offset=2", headers=headers).json()
                self.assertEqual(first["recent_total"], 3)
                self.assertEqual(len(first["recent"]), 2)
                self.assertTrue(first["recent_has_more"])
                self.assertEqual(len(second["recent"]), 1)
                self.assertFalse(second["recent_has_more"])
                self.assertFalse({item["event_id"] for item in first["recent"]}
                                 & {item["event_id"] for item in second["recent"]})
        finally:
            database.close()

    def test_count_tokens_api_requires_auth_csrf_and_explicit_synthetic_confirmation(self):
        app = create_app(token="isolated-count-tokens", vision_settings=self.store)
        headers = {"Authorization": "Bearer isolated-count-tokens",
                   "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token}
        with TestClient(app, base_url="https://testserver") as client:
            path = "/api/v1/vision/count-tokens"
            self.assertEqual(client.post(path, json={"confirm_synthetic_count_tokens": True}).status_code, 401)
            self.assertEqual(client.post(path, json={"confirm_synthetic_count_tokens": True},
                                         headers={"Authorization": headers["Authorization"]}).status_code, 403)
            self.assertEqual(client.post(path, json={"confirm_synthetic_count_tokens": False}, headers=headers).status_code, 428)
            with patch.object(self.store, "count_tokens_demo", return_value={"status": "unsupported"}) as probe:
                response = client.post(path, json={"confirm_synthetic_count_tokens": True}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "unsupported")
            probe.assert_called_once_with()

    def test_gemini_count_tokens_service_only_records_safe_synthetic_probe_metadata(self):
        database = SQLiteStore(self.root / "token-probe.db")
        manager = VisionSettingsStore(self.root / "token-probe-manager", protector=FakeProtector(), store=database)
        manager.save({"provider_id": "gemini",
                      "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/test-image:generateContent",
                      "model_id": "test-image"})
        manager.save_key("TEST_ONLY_TOKEN_PROBE_KEY")
        fake_reply = ProviderReply({"totalTokens": 321}, request_dispatched=True, http_status=200, api_elapsed_ms=10)
        try:
            with patch("meowkeeper.integrations.vision.gemini.GeminiVision.count_tokens", return_value=fake_reply):
                result = manager.count_tokens_demo()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["total_tokens"], 321)
            row = database.connection.execute(
                "SELECT outcome,http_status,total_tokens FROM vision_token_probes").fetchone()
            self.assertEqual(tuple(row), ("completed", 200, 321))
            self.assertNotIn("TEST_ONLY_TOKEN_PROBE_KEY", manager.path.read_text(encoding="utf-8"))
            self.assertNotIn("TEST_ONLY_TOKEN_PROBE_KEY", json.dumps(result))
            self.assertNotIn("jpeg", json.dumps([dict(row) for row in database.connection.execute(
                "SELECT * FROM vision_token_probes").fetchall()]).lower())
        finally:
            database.close()

    def test_usage_event_details_show_safe_candidate_and_delivery_summary(self):
        database = SQLiteStore(self.root / "usage-details.db")
        manager = VisionSettingsStore(self.root / "usage-details-manager", protector=FakeProtector(), store=database)
        event_id = "vision-event-details"
        base_time = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)

        def iso(seconds):
            return (base_time + timedelta(seconds=seconds)).isoformat()
        database.create_media_event(event_id=event_id, event_type="cat_detected", trigger_at=base_time,
                                    source_id="source-secret-like", track_id=None, pet_id="unknown", confidence=.91,
                                    requested_pre_seconds=1, requested_post_seconds=2)
        database._write("INSERT INTO media_event_tracks(event_id,track_id) VALUES(?,?)", (event_id, "track-a"))
        result_payload = {"status": "completed", "tracks": [{"track_id": "track-a", "tags": ["walking"]}],
                          "timings": {"queue_ms": 12, "provider_call_ms": 130,
                                      "queued_at": iso(0.1), "started_at": iso(0.2),
                                      "prepared_at": iso(0.5), "provider_started_at": iso(0.7),
                                      "http_started_at": iso(0.8), "response_received_at": iso(1.1),
                                      "finished_at": iso(1.3)}}
        database._write("INSERT INTO semantic_work_jobs(job_id,job_key,event_id,provider_id,model_id,state,queued_at,deadline_at,result_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (str(uuid4()), "job-key", event_id, "openai_compatible", "model-safe",
                                                       "completed", iso(0.1), 99_999_999,
                                                       json.dumps(result_payload)))
        database.record_delivery_trace(event_id, {
            "trace_id": str(uuid4()), "event_trigger_at": iso(0),
            "media_sendable_at": iso(1.5), "wait_started_at": iso(1.6),
            "wait_finished_at": iso(2.0), "validation_transcode_ms": 20,
            "extra_wait_ms": 100, "wait_budget_ms": 5000, "caption_source": "ai_candidate",
            "wait_status": "completed", "uploads": [{"recipient_ref": "a" * 64, "attempt": 1,
                "started_at": iso(2.1), "finished_at": iso(2.3),
                "elapsed_ms": 40, "accepted": True}], "omitted_upload_count": 0,
        })
        manager.save(self.settings())
        reference = manager.boundary(manager.read())
        job = manager.guard.reserve(reference, SemanticRequest(event_id, b"synthetic"), daily_limit=20,
                                    model_id="test-model")
        manager.guard.finish(job, "completed", request_dispatched=True, http_status=200, api_elapsed_ms=10)
        try:
            details = manager.usage(hours=24)["event_details"][event_id]
            self.assertEqual(details["tracks"][0]["tags"][0]["label"], "行走")
            self.assertEqual(details["analysis"]["timings_ms"]["queue_ms"], 12)
            self.assertEqual(details["telegram"]["caption_source"], "ai_candidate")
            self.assertEqual(details["telegram"]["accepted_uploads"], 1)
            self.assertEqual({lane["label"] for lane in details["timeline"]["lanes"]},
                             {"AI 分析", "影片與 Telegram", "上傳回條"})
            self.assertTrue(all(segment["duration_ms"] > 0
                                for lane in details["timeline"]["lanes"] for segment in lane["segments"]))
            self.assertNotIn("track_id", details["tracks"][0])
            serialized = json.dumps(manager.usage(hours=24), ensure_ascii=False)
            self.assertNotIn("source-secret-like", serialized)
            self.assertNotIn("recipient_ref", serialized)
        finally:
            database.close()

    def test_managed_provider_applies_saved_cross_event_cooldown(self):
        database = SQLiteStore(self.root / "managed-cooldown.db")
        manager = VisionSettingsStore(self.root / "managed-cooldown-manager", protector=FakeProtector(), store=database)
        for event_id, track_id in (("cooldown-one", "track-a"), ("cooldown-two", "track-a"),
                                   ("independent-track", "track-b")):
            database.create_media_event(event_id=event_id, event_type="cat_detected", trigger_at=datetime.now(timezone.utc),
                                        source_id="test", track_id=track_id, pet_id=None, confidence=.8,
                                        requested_pre_seconds=0, requested_post_seconds=1)
        manager.save(self.settings())
        manager.save({"event_merge_cooldown_seconds": 60})
        manager.save_key("TEST_ONLY_COOLDOWN_KEY")
        provider = manager.build_provider()
        board = TrackStoryboard("t", "track_01", b"synthetic", (0, .3, .6, .9), "epoch")
        reply = ProviderReply({"tracks": [{"track_alias": "track_01", "tags": []}]},
                              request_dispatched=True, http_status=200, api_elapsed_ms=10)
        try:
            with patch("meowkeeper.integrations.vision.providers.OpenAICompatibleVision.analyze", return_value=reply) as send:
                provider.analyze(SemanticRequest("cooldown-one", b"jpeg", ("t",), storyboards=(board,)), timeout_seconds=1)
                saved = database.connection.execute(
                    "SELECT request_dispatched,started_at FROM vision_request_jobs WHERE event_id='cooldown-one'"
                ).fetchone()
                self.assertEqual(manager.read()["event_merge_cooldown_seconds"], 60)
                self.assertEqual(next(iter(saved)), 1)
                with self.assertRaises(VisionFailure) as caught:
                    provider.analyze(SemanticRequest("cooldown-two", b"jpeg", ("t",), storyboards=(board,)), timeout_seconds=1)
                provider.analyze(SemanticRequest("independent-track", b"jpeg", ("t",), storyboards=(board,)), timeout_seconds=1)
            self.assertEqual(caught.exception.category, "skipped_cooldown")
            self.assertEqual(send.call_count, 2)
        finally:
            database.close()

    def test_demo_image_test_is_explicit_synthetic_and_recorded_without_returning_key(self):
        database = SQLiteStore(self.root / "demo-usage.db")
        manager = VisionSettingsStore(self.root / "demo-manager", protector=FakeProtector(), store=database)
        manager.save(self.settings())
        manager.save_key("TEST_ONLY_DEMO_KEY")
        app = create_app(token="isolated-demo-image", vision_settings=manager)
        headers = {"Authorization": "Bearer isolated-demo-image",
                   "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token}
        reply = ProviderReply({"tracks": [{"track_alias": "track_01", "tags": []}]},
                              request_dispatched=True, http_status=200, api_elapsed_ms=12,
                              input_tokens=24, output_tokens=3)
        try:
            with TestClient(app, base_url="https://testserver") as client:
                self.assertEqual(client.post("/api/v1/vision/image-test", json={
                    "confirm_demo_upload": True}, headers={"Authorization": headers["Authorization"]}).status_code, 403)
                with patch("meowkeeper.integrations.vision.openai_compatible.OpenAICompatibleVision.analyze",
                           return_value=reply):
                    response = client.post("/api/v1/vision/image-test", json={
                        "confirm_demo_upload": True}, headers=headers)
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["image_source"], "synthetic_local_demo")
                self.assertEqual(result["input_tokens"], 24)
                self.assertEqual(manager.usage(hours=24)["requests"], 1)
                self.assertNotIn("TEST_ONLY_DEMO_KEY", json.dumps(result))
                self.assertNotIn("TEST_ONLY_DEMO_KEY", manager.path.read_text(encoding="utf-8"))
        finally:
            database.close()

    def test_last_health_check_persists_only_safe_metadata(self):
        result = {"status": "model_not_confirmed", "http_status": 200,
                  "message_zh_tw": "已讀取模型資訊；這不是影像辨識品質的證明。"}
        self.store.health_path.parent.mkdir(parents=True, exist_ok=True)
        self.store._write_health(result)
        status = self.store.status()
        self.assertEqual(status["health_check"]["status"], "model_not_confirmed")
        self.assertEqual(status["health_check"]["http_status"], 200)
        self.assertNotIn("message", json.dumps(status["health_check"]))

    def test_runtime_auth_failure_not_reset_by_settings_save_only_key_change(self):
        database = SQLiteStore(self.root / "guard.db")
        manager = VisionSettingsStore(self.root / "managed", protector=FakeProtector(), store=database)
        for event in ("e1", "e2"):
            database.create_media_event(event_id=event, event_type="cat_detected", trigger_at=datetime.now(timezone.utc),
                                        source_id="test", track_id=None, pet_id=None, confidence=.8,
                                        requested_pre_seconds=0, requested_post_seconds=1)
        manager.save(self.settings())
        manager.save_key("TEST_ONLY_FIRST_KEY")
        provider = manager.build_provider()
        board = TrackStoryboard("t", "track_01", b"synthetic", (0, .3, .6, .9), "epoch")
        try:
            with patch("meowkeeper.integrations.vision.providers.OpenAICompatibleVision.analyze", side_effect=VisionFailure("auth_error", 401)), self.assertRaises(VisionFailure):
                provider.analyze(SemanticRequest("e1", b"jpeg", ("t",), storyboards=(board,)), timeout_seconds=1)
            manager.save({"grace_seconds": 6})
            self.assertTrue(manager.status()["policy"]["key_fix_required"])
            with patch("meowkeeper.integrations.vision.providers.OpenAICompatibleVision.analyze") as send:
                with self.assertRaises(VisionFailure) as caught:
                    provider.analyze(SemanticRequest("e2", b"jpeg", ("t",), storyboards=(board,)), timeout_seconds=1)
                self.assertEqual(caught.exception.category, "circuit_open")
                send.assert_not_called()
            manager.save_key("TEST_ONLY_REPLACEMENT_KEY")
            self.assertFalse(manager.status()["policy"]["key_fix_required"])
            self.assertEqual(manager.status()["policy"]["reserved_attempts"], 1)
        finally:
            database.close()

    def test_settings_page_mounts_live_vision_module_once(self):
        app = create_app(token="isolated-vision-page", vision_settings=self.store)
        with TestClient(app) as client:
            page = client.get("/settings/ai", headers={"Authorization": "Bearer isolated-vision-page"})
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.text.count('src="/static/js/vision-settings.js?'), 1)
            self.assertEqual(page.text.count('id="vision-settings-form"'), 1)

    def test_api_auth_csrf_confirmation_secret_redaction_and_page(self):
        app = create_app(token="isolated-vision-test", vision_settings=self.store)
        headers = {"Authorization": "Bearer isolated-vision-test", "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token}
        with TestClient(app, base_url="https://testserver") as client:
            self.assertEqual(client.get("/api/v1/vision/settings").status_code, 401)
            self.assertEqual(client.put("/api/v1/vision/settings", json=self.settings(),
                                       headers={"Authorization": headers["Authorization"]}).status_code, 403)
            invalid_gemini = client.put("/api/v1/vision/settings", json={
                "provider_id": "gemini",
                "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
                "model_id": "Gemini 3.5 Flash Lite",
            }, headers=headers)
            self.assertEqual(invalid_gemini.status_code, 422)
            self.assertIn("模型識別", invalid_gemini.json()["detail"])
            self.assertFalse(self.store.path.exists())
            self.assertEqual(client.put("/api/v1/vision/settings", json=self.settings(), headers=headers).status_code, 200)
            self.assertEqual(client.post("/api/v1/vision/credential", json={"key": "TEST_ONLY_KEY"}, headers=headers).status_code, 428)
            response = client.post("/api/v1/vision/credential", json={"key": "TEST_ONLY_KEY", "reauth": True}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("TEST_ONLY_KEY", response.text)
            page = client.get("/settings/ai", headers=headers).text
            self.assertIn('id="vision-settings-form"', page)
            self.assertEqual(page.count('id="vision-settings-form"'), 1)
            self.assertIn("照三步設定", page)
            self.assertIn('aria-describedby="vision-endpoint-help"', page)
            self.assertIn("服務 API 網址", page)
            self.assertIn("不傳短影片", page)
            self.assertIn('id="vision-media-resolution"', page)
            self.assertIn('id="vision-record-search"', page)
            self.assertIn('id="vision-record-identity"', page)
            self.assertIn('id="vision-record-telegram"', page)
            self.assertIn('id="vision-usage-more"', page)
            self.assertIn("分析紀錄與搜尋", page)
            self.assertNotIn('id="vision-image-detail"', page)
            self.assertEqual(client.post("/api/v1/vision/credential", json={"clear": True, "reauth": True}, headers=headers).status_code, 200)
            self.assertFalse(self.store.status()["key_configured"])
        with TestClient(app, base_url="http://testserver") as client:
            self.assertEqual(client.post("/api/v1/vision/credential", json={"key": "TEST_ONLY_KEY", "reauth": True}, headers=headers).status_code, 403)

    def test_private_lan_http_cannot_save_api_credentials(self):
        app = create_app(token="isolated-lan-credential", vision_settings=self.store)
        headers = {"Authorization": "Bearer isolated-lan-credential",
                   "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token}
        with TestClient(app, base_url="http://dashboard.test",
                        client=("192.168.1.20", 50000)) as client:
            response = client.post("/api/v1/vision/credential",
                                   json={"key": "TEST_ONLY_NEVER_SAVED", "reauth": True},
                                   headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertFalse(self.store.status()["key_configured"])

    def test_local_host_ip_allowed_to_save_credentials(self):
        import socket
        app = create_app(token="local-host-credential-test", vision_settings=self.store)
        headers = {"Authorization": "Bearer local-host-credential-test",
                   "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token}
        local_ip = socket.gethostbyname(socket.gethostname())
        with TestClient(app, base_url="http://dashboard.test",
                        client=(local_ip, 50000)) as client:
            response = client.post("/api/v1/vision/credential",
                                   json={"clear": True, "reauth": True},
                                   headers=headers)
            self.assertEqual(response.status_code, 200)

    @unittest.skipUnless(os.name == "nt", "需要 Windows Credential Manager")
    def test_actual_windows_credential_manager_roundtrip_and_cleanup(self):
        store = VisionSettingsStore(self.root / "credential-manager-test", credential_store=TelegramCredentialStore())
        store.save(self.settings(model_id=f"credential-test-{uuid4().hex}"))
        key = "TEST_ONLY_CREDENTIAL_PROBE_NOT_REAL"
        store.save_key(key)
        self.assertEqual(store.read_key(), key)
        self.assertTrue(store.status()["key_configured"])
        backend = store.status()["credential_backend"]
        self.assertIn(backend, {"windows_credential_manager", "windows_dpapi_fallback"})
        if backend == "windows_dpapi_fallback":
            self.assertNotIn(key.encode(), store._secret_path(store.read()).read_bytes())
        else:
            self.assertFalse(store._secret_path(store.read()).exists())
        store.clear_key()
        self.assertIsNone(store.read_key())


    def test_multiple_keys_management_and_sequential_fallback(self):
        store = self.store
        store.save(self.settings())
        # 1. Add first key
        k1 = store.add_key("KEY_ONE_PRIMARY", alias="主要金鑰")
        self.assertEqual(k1["alias"], "主要金鑰")
        self.assertEqual(store.read_key(), "KEY_ONE_PRIMARY")
        keys = store.read_keys()
        self.assertEqual(len(keys), 1)

        # 2. Add second key
        k2 = store.add_key("KEY_TWO_BACKUP", alias="備用金鑰")
        keys = store.read_keys()
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0]["key"], "KEY_ONE_PRIMARY")
        self.assertEqual(keys[1]["key"], "KEY_TWO_BACKUP")

        # 3. Status masks keys
        status = store.status()
        self.assertTrue(status["key_configured"])
        self.assertEqual(len(status["keys"]), 2)
        self.assertEqual(status["keys"][0]["alias"], "主要金鑰")
        self.assertEqual(status["keys"][1]["alias"], "備用金鑰")
        self.assertNotIn("KEY_ONE_PRIMARY", json.dumps(status))
        self.assertNotIn("KEY_TWO_BACKUP", json.dumps(status))

        # 4. Reorder keys
        store.reorder_keys([k2["id"], k1["id"]])
        self.assertEqual(store.read_key(), "KEY_TWO_BACKUP")
        reordered_keys = store.read_keys()
        self.assertEqual(reordered_keys[0]["key"], "KEY_TWO_BACKUP")
        self.assertEqual(reordered_keys[1]["key"], "KEY_ONE_PRIMARY")

        # 5. Delete key
        store.delete_key(k1["id"])
        remaining = store.read_keys()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["key"], "KEY_TWO_BACKUP")

        # 6. Fallback in check_connection: first key fails, second succeeds
        store.reorder_keys([])
        store.clear_key()
        k_bad = store.add_key("KEY_EXHAUSTED", alias="已耗盡金鑰")
        k_good = store.add_key("KEY_VALID", alias="有效備用金鑰")

        call_keys = []
        def fake_health_check(provider_id, settings, key, timeout_seconds=4):
            call_keys.append(key)
            if key == "KEY_EXHAUSTED":
                return {"status": "rate_limited", "http_status": 402, "message_zh_tw": "額度已用盡"}
            return {"status": "metadata_available", "http_status": 200, "models": ["test-model"]}

        store._last_health_check = float("-inf")
        with patch("meowkeeper.app.vision_settings.health_check", side_effect=fake_health_check):
            result = store.check_connection()
        self.assertEqual(result["status"], "metadata_available")
        self.assertEqual(result["active_key_alias"], "有效備用金鑰")
        self.assertEqual(call_keys, ["KEY_EXHAUSTED", "KEY_VALID"])

        # 7. Per-key call statistics are recorded, exposed without secrets, and resettable
        status = store.status()
        by_id = {item["id"]: item for item in status["keys"]}
        self.assertEqual(by_id[k_bad["id"]]["stats"]["calls"], 1)
        self.assertEqual(by_id[k_bad["id"]]["stats"]["failures"], 1)
        self.assertEqual(by_id[k_bad["id"]]["stats"]["last_http_status"], 402)
        self.assertEqual(by_id[k_bad["id"]]["stats"]["failure_categories"], {"rate_limited": 1})
        self.assertEqual(by_id[k_good["id"]]["stats"]["successes"], 1)
        self.assertEqual(by_id[k_good["id"]]["stats"]["by_operation"]["health_check"],
                         {"calls": 1, "successes": 1, "failures": 0})
        self.assertNotIn("KEY_VALID", store.key_stats_path.read_text(encoding="utf-8"))
        store.delete_key(k_bad["id"])
        self.assertNotIn(k_bad["id"], store.key_stats())
        store.reset_key_stats()
        self.assertIsNone(store.status()["keys"][0]["stats"])

    def test_managed_provider_records_per_key_attempts_on_fallback(self):
        database = SQLiteStore(self.root / "key-stats.db")
        manager = VisionSettingsStore(self.root / "key-stats-manager", protector=FakeProtector(), store=database)
        manager.save(self.settings())
        first = manager.add_key("TEST_ONLY_LIMITED", alias="限流")
        second = manager.add_key("TEST_ONLY_OK", alias="正常")
        board = TrackStoryboard("t", "track_01", b"synthetic", (0, .3), "epoch")
        reply = ProviderReply({"tracks": [{"track_alias": "track_01", "tags": []}]},
                              request_dispatched=True, http_status=200, api_elapsed_ms=10)
        calls = []

        def fake_analyze(provider_self, request, *, timeout_seconds):
            key = provider_self._credential()
            calls.append(key)
            if key == "TEST_ONLY_LIMITED":
                raise VisionFailure("rate_limited", 429, request_dispatched=True)
            return reply

        try:
            with patch("meowkeeper.integrations.vision.providers.OpenAICompatibleVision.analyze", fake_analyze):
                manager.build_provider().analyze(
                    SemanticRequest("stats-event", b"jpeg", ("t",), storyboards=(board,)), timeout_seconds=1)
            self.assertEqual(calls, ["TEST_ONLY_LIMITED", "TEST_ONLY_OK"])
            stats = manager.key_stats()
            self.assertEqual(stats[first["id"]]["by_operation"]["analysis"], {"calls": 1, "successes": 0, "failures": 1})
            self.assertEqual(stats[first["id"]]["last_http_status"], 429)
            self.assertEqual(stats[second["id"]]["by_operation"]["analysis"], {"calls": 1, "successes": 1, "failures": 0})
        finally:
            database.close()

    def test_local_http_endpoint_without_key_still_dispatches(self):
        database = SQLiteStore(self.root / "keyless.db")
        manager = VisionSettingsStore(self.root / "keyless-manager", protector=FakeProtector(), store=database)
        manager.save(self.settings(endpoint="http://127.0.0.1:11434/v1/chat/completions"))
        board = TrackStoryboard("t", "track_01", b"synthetic", (0, .3), "epoch")
        reply = ProviderReply({"tracks": [{"track_alias": "track_01", "tags": []}]},
                              request_dispatched=True, http_status=200, api_elapsed_ms=10)
        try:
            with patch("meowkeeper.integrations.vision.providers.OpenAICompatibleVision.analyze",
                       return_value=reply) as send:
                result = manager.build_provider().analyze(
                    SemanticRequest("keyless-event", b"jpeg", ("t",), storyboards=(board,)), timeout_seconds=1)
            self.assertIs(result, reply)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(manager.status()["keyless_stats"]["successes"], 1)
        finally:
            database.close()

if __name__ == "__main__":
    unittest.main()
