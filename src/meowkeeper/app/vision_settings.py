"""非秘密視覺設定、外送同意與目前使用者的 DPAPI 金鑰。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from meowkeeper.core.event_semantics import (
    TAG_LABELS,
    ProviderReply,
    SemanticRequest,
    VisionFailure,
)
from meowkeeper.core.semantic_storyboard import TrackStoryboard
from meowkeeper.core.vision_request_guard import VisionRequestGuard
from meowkeeper.integrations.vision.openai_compatible import VisionEndpoint
from meowkeeper.integrations.vision.providers import PROVIDERS, capabilities, health_check
from meowkeeper.security.credentials import CredentialStoreError, TelegramCredentialStore

QUOTA_FIELDS = ("daily_request_limit", "requests_per_minute_limit", "tokens_per_minute_limit",
                "provider_daily_request_limit", "provider_requests_per_minute_limit",
                "provider_tokens_per_minute_limit", "estimated_tokens_per_request",
                "warning_threshold_percent")
QUOTA_DEFAULTS = {"daily_request_limit": 400, "requests_per_minute_limit": 12,
                  "tokens_per_minute_limit": 0, "provider_daily_request_limit": 0,
                  "provider_requests_per_minute_limit": 0, "provider_tokens_per_minute_limit": 0,
                  "estimated_tokens_per_request": 0, "warning_threshold_percent": 80}
QUALITY_FIELDS = ("quality_preset", "storyboard_frame_count", "storyboard_size",
                  "storyboard_jpeg_quality", "storyboard_timestamps", "storyboard_include_zones",
                  "storyboard_media_resolution", "event_merge_cooldown_seconds",
                  "storyboard_include_scene_references")
QUALITY_DEFAULTS = {"quality_preset": "balanced", "storyboard_frame_count": 4,
                    "storyboard_size": "1536x1024", "storyboard_jpeg_quality": 82,
                    "storyboard_timestamps": True, "storyboard_include_zones": True,
                    "storyboard_media_resolution": "auto", "event_merge_cooldown_seconds": 0,
                    "storyboard_include_scene_references": False}
LEGACY_QUALITY_DEFAULTS_V5 = {
    "quality_preset": "balanced", "storyboard_frame_count": 4, "storyboard_size": "1536x1024",
    "storyboard_jpeg_quality": 82, "storyboard_timestamps": True, "storyboard_include_zones": True,
    "storyboard_image_detail": "provider_default", "event_merge_cooldown_seconds": 0,
}
PROFILE_FIELDS = (*QUOTA_FIELDS, *QUALITY_FIELDS)
DEFAULTS = {"schema_version": 7, "provider_id": "openai_compatible", "endpoint": "", "model_id": "",
            "enabled": False, "json_object_supported": False, "grace_seconds": 5,
            "consent_reference": None, "consent_at": None,
            "scene_reference_consent_reference": None, "scene_reference_consent_at": None,
            **QUOTA_DEFAULTS, **QUALITY_DEFAULTS,
            "quota_profiles": {}}


class VisionSettingsStore:
    def __init__(self, data_root: Path, *, protector=None, store=None, credential_store=None):
        self.path = Path(data_root) / "state" / "vision-analysis.json"
        self.health_path = Path(data_root) / "state" / "vision-health.json"
        self.secret_directory = Path(data_root) / "secrets" / "vision"
        self._protector = protector
        self._credential_store = credential_store
        self._last_credential_backend = "windows_credential_manager"
        self._lock = threading.RLock()
        self.guard = VisionRequestGuard(store) if store is not None else None
        self._last_health_check = float("-inf")

    @staticmethod
    def boundary(values):
        return hashlib.sha256(json.dumps([values["provider_id"], values["endpoint"], values["model_id"]],
                                        separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def provider_image_detail(values):
        if values["provider_id"] != "openai_compatible":
            return "provider_default"
        resolution = values["storyboard_media_resolution"]
        return "provider_default" if resolution == "auto" else resolution

    @staticmethod
    def effective_canvas_size(values):
        if values["provider_id"] == "gemini":
            return {"low": "1280x960", "medium": "1536x1024", "high": "2048x1365"}.get(
                values["storyboard_media_resolution"], values["storyboard_size"])
        return values["storyboard_size"]

    def event_cooldown_ref(self, provider_ref, event_id):
        """Return an opaque scope only when the same source/track can be proven."""
        if self.guard is None or not isinstance(event_id, str) or not event_id:
            return None
        with self.guard.store._connection_lock:
            row = self.guard.store.connection.execute(
                "SELECT event_type,source_id,track_id,pet_id FROM media_events WHERE event_id=? LIMIT 1",
                (event_id,),
            ).fetchone()
        if row is None or not row["track_id"] or not row["source_id"] or not row["event_type"]:
            return None
        # Tracker identity is camera-scoped. Including known pet identity avoids
        # coalescing events whose resolved identities conflict; unknown remains
        # unknown and never causes cross-track merging.
        identity = row["pet_id"] if row["pet_id"] not in {None, "", "unknown"} else "unknown"
        scope = [provider_ref, row["event_type"], row["source_id"], str(row["track_id"]), identity]
        return hashlib.sha256(json.dumps(scope, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()

    def read(self):
        with self._lock:
            if not self.path.exists():
                return dict(DEFAULTS)
            if self.path.stat().st_size > 16 * 1024:
                raise ValueError("視覺分析設定檔過大。")
            values = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(values, dict) and values.get("schema_version") == 1:
                values = {**values, "schema_version": 2, "daily_request_limit": 100}
            if isinstance(values, dict) and values.get("schema_version") == 2:
                old_fields = set(DEFAULTS) - {"quota_profiles", *PROFILE_FIELDS,
                                             "scene_reference_consent_reference", "scene_reference_consent_at"}
                if set(values) == old_fields | {"daily_request_limit"}:
                    values = {**values, "schema_version": 3, **{key: value for key, value in QUOTA_DEFAULTS.items()
                                                                 if key != "daily_request_limit"},
                              "quota_profiles": {}}
            if isinstance(values, dict) and values.get("schema_version") == 3:
                profiles = values.get("quota_profiles", {})
                if isinstance(profiles, dict):
                    profiles = {reference: {**profile, **{key: value for key, value in QUOTA_DEFAULTS.items()
                                                           if key == "warning_threshold_percent"},
                                             **LEGACY_QUALITY_DEFAULTS_V5}
                                for reference, profile in profiles.items() if isinstance(profile, dict)}
                values = {**values, "schema_version": 4, **LEGACY_QUALITY_DEFAULTS_V5,
                          "warning_threshold_percent": QUOTA_DEFAULTS["warning_threshold_percent"],
                          "quota_profiles": profiles}
            if isinstance(values, dict) and values.get("schema_version") == 4:
                profiles = values.get("quota_profiles", {})
                if isinstance(profiles, dict):
                    profiles = {reference: {**LEGACY_QUALITY_DEFAULTS_V5, **profile}
                                for reference, profile in profiles.items() if isinstance(profile, dict)}
                values = {**LEGACY_QUALITY_DEFAULTS_V5, **values, "schema_version": 5, "quota_profiles": profiles}
            if isinstance(values, dict) and values.get("schema_version") == 5:
                profiles = values.get("quota_profiles", {})
                if isinstance(profiles, dict):
                    migrated_profiles = {}
                    for reference, profile in profiles.items():
                        if not isinstance(profile, dict):
                            continue
                        migrated = dict(profile)
                        migrated.pop("storyboard_image_detail", None)
                        migrated["storyboard_media_resolution"] = "auto"
                        migrated_profiles[reference] = migrated
                    profiles = migrated_profiles
                migrated = dict(values)
                migrated.pop("storyboard_image_detail", None)
                migrated["storyboard_media_resolution"] = "auto"
                values = {**migrated, "schema_version": 6, "quota_profiles": profiles}
            if isinstance(values, dict) and values.get("schema_version") == 6:
                profiles = values.get("quota_profiles", {})
                if isinstance(profiles, dict):
                    profiles = {reference: {**QUALITY_DEFAULTS, **profile}
                                for reference, profile in profiles.items() if isinstance(profile, dict)}
                values = {**values, "schema_version": 7,
                          "storyboard_include_scene_references": False,
                          "scene_reference_consent_reference": None,
                          "scene_reference_consent_at": None,
                          "quota_profiles": profiles}
            if not isinstance(values, dict) or set(values) != set(DEFAULTS) or values["schema_version"] != 7:
                raise ValueError("視覺分析設定版本不相容。")
            self._validate(values)
            return values

    @staticmethod
    def _validate(values):
        if not isinstance(values["provider_id"],str) or values["provider_id"] not in PROVIDERS:
            raise ValueError("目前尚未提供此視覺服務協定。")
        limits = {"daily_request_limit": (1, 500), "requests_per_minute_limit": (1, 60),
                  "tokens_per_minute_limit": (0, 100_000_000), "provider_daily_request_limit": (0, 10_000_000),
                  "provider_requests_per_minute_limit": (0, 10000), "provider_tokens_per_minute_limit": (0, 100_000_000),
                  "estimated_tokens_per_request": (0, 100_000_000), "warning_threshold_percent": (50, 95)}
        for name, (minimum, maximum) in limits.items():
            if type(values[name]) is not int or not minimum <= values[name] <= maximum:
                raise ValueError("AI 額度設定超出允許範圍。")
        if type(values["grace_seconds"]) not in {int, float} or not 0 <= values["grace_seconds"] <= 10:
            raise ValueError("通知額外等待只允許 0～10 秒。")
        if type(values["enabled"]) is not bool or type(values["json_object_supported"]) is not bool:
            raise ValueError("視覺分析開關格式不正確。")
        if (values["quality_preset"] not in {"economy", "balanced", "detail", "custom"}
                or type(values["storyboard_frame_count"]) is not int
                or not 2 <= values["storyboard_frame_count"] <= 8
                or values["storyboard_size"] not in {"1280x960", "1536x1024", "2048x1365"}
                or type(values["storyboard_jpeg_quality"]) is not int
                or not 60 <= values["storyboard_jpeg_quality"] <= 95
                or values["storyboard_media_resolution"] not in {"auto", "low", "medium", "high"}
                or type(values["event_merge_cooldown_seconds"]) is not int
                or not 0 <= values["event_merge_cooldown_seconds"] <= 3600
                or type(values["storyboard_timestamps"]) is not bool
                or type(values["storyboard_include_zones"]) is not bool
                or type(values["storyboard_include_scene_references"]) is not bool):
            raise ValueError("圖片分析品質設定不正確。")
        if values["provider_id"] == "openai_compatible" and values["storyboard_media_resolution"] == "medium":
            raise ValueError("目前 OpenAI 格式相容服務沒有通用的「中」細節選項；請改用自動、低或高。")
        endpoint, model = values["endpoint"], values["model_id"]
        if not isinstance(endpoint, str) or not isinstance(model, str):
            raise TypeError("服務網址與模型 ID 必須是文字。")
        if values["enabled"] and (not endpoint or not model):
            raise ValueError("啟用圖片分析前，請先填完整的服務網址與模型 ID。")
        if endpoint or model:
            # Validate each supplied draft field even when the disabled setup
            # is incomplete. These placeholders are never used for requests.
            settings = VisionEndpoint(endpoint or "https://example.invalid", model or "draft")
            if values["provider_id"] == "gemini" and model and not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", model):
                raise ValueError("Gemini 模型 ID 格式不正確；請使用服務商提供的識別碼。")
            if endpoint and model:
                PROVIDERS[values["provider_id"]](settings, lambda:None)
        if values["enabled"] and (values["consent_reference"] != VisionSettingsStore.boundary(values)
                                  or not isinstance(values["consent_at"], str)):
            raise ValueError("請先明確同意將必要貓框圖片交給目前設定的服務。")
        if values["enabled"] and values["storyboard_include_scene_references"] and (
                values["scene_reference_consent_reference"] != VisionSettingsStore.boundary(values)
                or not isinstance(values["scene_reference_consent_at"], str)):
            raise ValueError("如要附送場景參考拼貼，請先勾選並同意送出已登錄物件裁切與名稱。")
        if (values["scene_reference_consent_reference"] is not None
                and (not isinstance(values["scene_reference_consent_reference"], str)
                     or not isinstance(values["scene_reference_consent_at"], str))):
            raise ValueError("場景參考圖片同意紀錄格式不正確。")
        profiles = values["quota_profiles"]
        if not isinstance(profiles, dict) or len(profiles) > 30:
            raise ValueError("每模型額度設定格式不正確。")
        for reference, profile in profiles.items():
            if not isinstance(reference, str) or len(reference) != 64 or any(char not in "0123456789abcdef" for char in reference):
                raise ValueError("每模型額度識別不正確。")
            if not isinstance(profile, dict) or set(profile) != set(PROFILE_FIELDS) | {"updated_at"}:
                raise ValueError("每模型額度資料不完整。")
            VisionSettingsStore._validate_quota_profile(profile)

    @staticmethod
    def _validate_quota_profile(profile):
        ranges = {"daily_request_limit": (1, 500), "requests_per_minute_limit": (1, 60),
                  "tokens_per_minute_limit": (0, 100_000_000), "provider_daily_request_limit": (0, 10_000_000),
                  "provider_requests_per_minute_limit": (0, 10000), "provider_tokens_per_minute_limit": (0, 100_000_000),
                  "estimated_tokens_per_request": (0, 100_000_000), "warning_threshold_percent": (50, 95)}
        for name, (minimum, maximum) in ranges.items():
            if type(profile[name]) is not int or not minimum <= profile[name] <= maximum:
                raise ValueError("每模型額度設定超出允許範圍。")
        if (profile["quality_preset"] not in {"economy", "balanced", "detail", "custom"}
                or type(profile["storyboard_frame_count"]) is not int
                or not 2 <= profile["storyboard_frame_count"] <= 8
                or profile["storyboard_size"] not in {"1280x960", "1536x1024", "2048x1365"}
                or type(profile["storyboard_jpeg_quality"]) is not int
                or not 60 <= profile["storyboard_jpeg_quality"] <= 95
                or profile["storyboard_media_resolution"] not in {"auto", "low", "medium", "high"}
                or type(profile["event_merge_cooldown_seconds"]) is not int
                or not 0 <= profile["event_merge_cooldown_seconds"] <= 3600
                or type(profile["storyboard_timestamps"]) is not bool
                or type(profile["storyboard_include_zones"]) is not bool
                or type(profile["storyboard_include_scene_references"]) is not bool):
            raise ValueError("每模型圖片品質設定不正確。")
        if not isinstance(profile["updated_at"], str):
            raise TypeError("每模型額度時間格式不正確。")

    def save(self, payload):
        editable = {"provider_id", "endpoint", "model_id", "enabled", "json_object_supported", "grace_seconds",
                    *PROFILE_FIELDS}
        if not isinstance(payload, dict) or set(payload) - editable - {"confirm_upload", "confirm_scene_reference_upload"}:
            raise ValueError("視覺設定含未支援欄位；金鑰需由安全保存操作處理。")
        with self._lock:
            values = self.read()
            before_values = dict(values)
            before = self.boundary(values)
            profiles = dict(values["quota_profiles"])
            candidate = dict(values)
            candidate.update({key: value for key, value in payload.items() if key in {"provider_id", "endpoint", "model_id"}})
            after = self.boundary(candidate)
            boundary_changed = before != after
            if boundary_changed:
                profiles[before] = {**{key: values[key] for key in PROFILE_FIELDS},
                                    "updated_at": datetime.now(timezone.utc).isoformat()}
                target_profile = profiles.get(after)
                values.update(target_profile or {**QUOTA_DEFAULTS, **QUALITY_DEFAULTS, "updated_at": ""})
                values.pop("updated_at", None)
            # The target profile is the baseline; explicitly submitted edits
            # always win, including on the same save as a destination change.
            values.update({key: value for key, value in payload.items() if key in editable})
            values["quota_profiles"] = profiles
            after = self.boundary(values)
            if before != after:
                values["consent_reference"] = values["consent_at"] = None
                values["scene_reference_consent_reference"] = values["scene_reference_consent_at"] = None
            if payload.get("confirm_upload") is True:
                values["consent_reference"] = after
                values["consent_at"] = datetime.now(timezone.utc).isoformat()
            if payload.get("confirm_scene_reference_upload") is True:
                values["scene_reference_consent_reference"] = after
                values["scene_reference_consent_at"] = datetime.now(timezone.utc).isoformat()
            if not values["storyboard_include_scene_references"] or values["enabled"] is False:
                values["scene_reference_consent_reference"] = values["scene_reference_consent_at"] = None
            if values["enabled"] is False:
                values["consent_reference"] = values["consent_at"] = None
            values["quota_profiles"][after] = {**{key: values[key] for key in PROFILE_FIELDS},
                                                "updated_at": datetime.now(timezone.utc).isoformat()}
            if len(values["quota_profiles"]) > 30:
                oldest = sorted(values["quota_profiles"].items(), key=lambda item: item[1]["updated_at"])
                for reference, _profile in oldest[:len(values["quota_profiles"])-30]:
                    if reference != after:
                        values["quota_profiles"].pop(reference, None)
            self._validate(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                    json.dump(values, file, ensure_ascii=False, indent=2)
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            if (
                before != after
                and before_values.get("provider_id") == values.get("provider_id")
                and bool(values.get("endpoint"))
                and bool(values.get("model_id"))
                and (
                    before_values.get("endpoint") == values.get("endpoint")
                    or (
                        values.get("provider_id") == "gemini"
                        and urlsplit(before_values.get("endpoint") or "").netloc == "generativelanguage.googleapis.com"
                        and urlsplit(values.get("endpoint") or "").netloc == "generativelanguage.googleapis.com"
                    )
                )
            ):
                try:
                    if not self._key_configured(values):
                        existing_key = self._read_key_for(before_values)
                        if existing_key:
                            self._save_key_for(values, existing_key)
                except Exception:
                    pass
            return self.status()

    def _secret_path(self, values):
        return self.secret_directory / (self.boundary(values) + ".dpapi")

    def _credential_manager(self):
        if self._credential_store is None:
            self._credential_store = TelegramCredentialStore()
        return self._credential_store

    def _read_dpapi_key_file(self, values):
        target = self._secret_path(values)
        if not target.is_file():
            return None
        if target.stat().st_size > 64 * 1024:
            raise CredentialStoreError("視覺服務金鑰檔案過大。")
        return self._crypt(target.read_bytes(), protect=False).decode("utf-8")

    def _write_dpapi_key_file(self, values, key):
        encrypted = self._crypt(key.encode("utf-8"), protect=True)
        self.secret_directory.mkdir(parents=True, exist_ok=True)
        target = self._secret_path(values)
        descriptor, temporary = tempfile.mkstemp(dir=self.secret_directory, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(encrypted)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _crypt(self, value, *, protect):
        protector = self._protector or TelegramCredentialStore()
        return protector._dpapi(value, protect=protect)

    def _save_key_for(self, values, key):
        VisionEndpoint(values["endpoint"], values["model_id"])
        if self._protector is not None:
            # Test-only adapter; production credentials are stored in Windows Credential Manager.
            self._write_dpapi_key_file(values, key)
        else:
            try:
                self._credential_manager().save_vision_key(self.boundary(values), key)
                self._last_credential_backend = "windows_credential_manager"
                self._secret_path(values).unlink(missing_ok=True)
            except CredentialStoreError as error:
                if error.code not in {None, 1312}:
                    raise
                # SYSTEM services can lack an interactive Credential Manager
                # logon session (1312). Keep this key DPAPI-protected under
                # the application data root instead of failing or storing it
                # as plaintext.
                self._write_dpapi_key_file(values, key)
                self._last_credential_backend = "windows_dpapi_fallback"
        if self.guard is not None:
            self.guard.key_replaced(self.boundary(values))

    def save_key(self, key):
        if not isinstance(key, str) or not key.strip() or not 1 <= len(key) <= 4096 or "\r" in key or "\n" in key:
            raise ValueError("API Key 不可為空白、過長或含換行。")
        with self._lock:
            values = self.read()
            self._save_key_for(values, key)

    def read_key(self):
        with self._lock:
            values = self.read()
            return self._read_key_for(values)

    def _read_key_for(self, values):
        target = self._secret_path(values)
        if self._protector is not None:
            return self._read_dpapi_key_file(values)
        reference = self.boundary(values)
        credential_store = self._credential_manager()
        try:
            value = credential_store.read_vision_key(reference)
        except CredentialStoreError as error:
            if error.code not in {None, 1312}:
                raise
            self._last_credential_backend = "windows_dpapi_fallback"
            return self._read_dpapi_key_file(values)
        if value:
            self._last_credential_backend = "windows_credential_manager"
            target.unlink(missing_ok=True)
            return value
        fallback = self._read_dpapi_key_file(values)
        if fallback is None:
            self._last_credential_backend = "windows_credential_manager"
            return None
        # Migrate the earlier encrypted-file value when Credential Manager is
        # usable. Preserve the DPAPI file if the write still fails with 1312.
        try:
            credential_store.save_vision_key(reference, fallback)
        except CredentialStoreError as error:
            if error.code not in {None, 1312}:
                raise
            self._last_credential_backend = "windows_dpapi_fallback"
            return fallback
        target.unlink(missing_ok=True)
        self._last_credential_backend = "windows_credential_manager"
        return fallback

    def _key_configured(self, values):
        if self._protector is not None:
            return self._secret_path(values).is_file()
        return bool(self._read_key_for(values))

    @staticmethod
    def _demo_cat_jpeg():
        """Draw a synthetic sample in memory; never reads a camera or user file."""
        from io import BytesIO

        from PIL import Image, ImageDraw

        image = Image.new("RGB", (640, 360), "#f5eee5")
        draw = ImageDraw.Draw(image)
        draw.ellipse((170, 156, 470, 330), fill="#c7804b")
        draw.ellipse((376, 94, 545, 263), fill="#d99661")
        draw.polygon(((395, 136), (405, 60), (451, 116)), fill="#d99661")
        draw.polygon(((472, 114), (519, 57), (530, 155)), fill="#d99661")
        draw.polygon(((407, 116), (412, 82), (434, 118)), fill="#e9b69a")
        draw.polygon(((487, 111), (512, 83), (518, 130)), fill="#e9b69a")
        draw.ellipse((430, 158, 444, 174), fill="#33271f")
        draw.ellipse((486, 158, 500, 174), fill="#33271f")
        draw.polygon(((457, 183), (469, 183), (463, 191)), fill="#7a3825")
        draw.arc((446, 187, 465, 205), 0, 130, fill="#4a3228", width=3)
        draw.arc((462, 187, 481, 205), 45, 180, fill="#4a3228", width=3)
        for y in (181, 194, 207):
            draw.line((438, y, 373, y - 10), fill="#725b4b", width=2)
            draw.line((488, y, 554, y - 10), fill="#725b4b", width=2)
        draw.ellipse((192, 167, 253, 227), fill="#fffdf9")
        draw.ellipse((210, 181, 220, 196), fill="#342922")
        draw.text((18, 18), "MeowKeeper+ 合成示範圖（非居家影像）", fill="#4a3b30")
        output = BytesIO()
        image.save(output, format="JPEG", quality=82)
        return output.getvalue()

    def test_demo_image(self):
        """Perform one explicitly requested image-capability call and meter it."""
        if self.guard is None:
            return {"status": "unavailable", "message_zh_tw": "此執行環境沒有 SQLite 額度帳本，未送出測試。"}
        with self._lock:
            values = self.read()
            if not values["endpoint"] or not values["model_id"]:
                return {"status": "unavailable", "message_zh_tw": "請先保存服務網址與圖片模型，再測試。"}
            try:
                settings = VisionEndpoint(values["endpoint"], values["model_id"], enabled=True,
                                          upload_consent=True,
                                          json_object_supported=values["json_object_supported"],
                                          image_detail=self.provider_image_detail(values))
                key = self._read_key_for(values)
            except (OSError, ValueError, RuntimeError, CredentialStoreError):
                return {"status": "auth_error", "message_zh_tw": "服務網址或安全金鑰狀態不正確；未送出測試。"}
            if urlsplit(values["endpoint"]).scheme == "https" and not key:
                return {"status": "auth_error", "message_zh_tw": "此服務需要 API Key；請先安全保存金鑰。未送出測試。"}
            provider_id = values["provider_id"]
            reference = self.boundary(values)
            limits = self.quota_limits(values)
        jpeg = self._demo_cat_jpeg()
        now = datetime.now(timezone.utc)
        storyboard = TrackStoryboard("demo-cat", "track_01", jpeg, (-0.5, 0.0), "local-demo")
        request = SemanticRequest(str(uuid4()), jpeg, ("demo-cat",), now, (storyboard,))
        try:
            job = self.guard.reserve(reference, request, daily_limit=limits["daily_limit"],
                                     rpm_limit=limits["rpm_limit"], tpm_limit=limits["tpm_limit"],
                                     estimated_tokens=limits["estimated_tokens_per_request"] or None,
                                     model_id=values["model_id"], reset_timezone=limits["reset_timezone"])
        except VisionFailure as error:
            return {"status": error.category, "message_zh_tw": "本機額度或服務保護暫不允許測試；沒有送出示範圖。"}
        started = time.monotonic()
        try:
            result = PROVIDERS[provider_id](settings, lambda: key).analyze(request, timeout_seconds=15)
            if not isinstance(result, ProviderReply):
                raise VisionFailure("bad_response", request_dispatched=False)
            elapsed = (time.monotonic() - started) * 1000
            self.guard.finish(job, "completed", request_dispatched=result.request_dispatched,
                              http_status=result.http_status,
                              api_elapsed_ms=result.api_elapsed_ms if result.api_elapsed_ms is not None else elapsed,
                              input_tokens=result.input_tokens, output_tokens=result.output_tokens)
            tracks = result.get("tracks")
            return {"status": "completed", "http_status": result.http_status,
                    "request_dispatched": result.request_dispatched, "elapsed_ms": round(elapsed),
                    "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                    "track_count": len(tracks) if isinstance(tracks, list) else 0,
                    "image_source": "synthetic_local_demo",
                    "message_zh_tw": "示範插圖已完成圖片 API 回應解析；這只證明本次示範圖請求成功，不代表居家圖片辨識準確。"}
        except VisionFailure as error:
            self.guard.finish(job, error.category, retry_after=error.retry_after_seconds,
                              request_dispatched=error.request_dispatched, http_status=error.http_status,
                              api_elapsed_ms=error.api_elapsed_ms)
            return {"status": error.category, "http_status": error.http_status,
                    "request_dispatched": error.request_dispatched,
                    "message_zh_tw": "示範圖片測試未完成；請依狀態檢查金鑰、模型、格式、額度或網路。居家監控與 Telegram 不受影響。"}
        except (OSError, RuntimeError, TypeError, ValueError):
            self.guard.finish(job, "unavailable", request_dispatched=False)
            return {"status": "unavailable", "request_dispatched": False,
                    "message_zh_tw": "示範圖片測試未完成；未保存服務原始回應，居家監控與 Telegram 不受影響。"}

    def count_tokens_demo(self):
        """Explicit Gemini countTokens request using one synthetic, local-only demo image."""
        if self.guard is None:
            return {"status": "unavailable", "message_zh_tw": "此執行環境沒有探測帳本；未送出請求。"}
        with self._lock:
            values = self.read()
            if values["provider_id"] != "gemini":
                return {"status": "unsupported", "message_zh_tw": "目前只有 Gemini GenerateContent 提供已接線的 countTokens 探測；沒有呼叫其他服務。"}
            if not values["endpoint"] or not values["model_id"]:
                return {"status": "unavailable", "message_zh_tw": "請先保存 Gemini 模型設定；未送出探測。"}
            try:
                key = self.read_key()
                settings = VisionEndpoint(values["endpoint"], values["model_id"], enabled=True,
                                          upload_consent=True, json_object_supported=values["json_object_supported"],
                                          image_detail="provider_default")
                from meowkeeper.integrations.vision.gemini import GeminiVision
                provider = GeminiVision(settings, lambda: key)
            except (OSError, ValueError, RuntimeError, CredentialStoreError):
                return {"status": "auth_error", "message_zh_tw": "Gemini 網址或安全金鑰狀態不正確；未送出探測。"}
            if urlsplit(values["endpoint"]).scheme == "https" and not key:
                return {"status": "auth_error", "message_zh_tw": "Gemini 需要 API Key；請先安全保存金鑰。未送出探測。"}
            reference = self.boundary(values)
            model_id = values["model_id"]
            frame_count = values["storyboard_frame_count"]
            canvas_size = self.effective_canvas_size(values)
            jpeg_quality = values["storyboard_jpeg_quality"]
        try:
            probe_id = self.guard.reserve_token_probe(reference, model_id)
        except VisionFailure as error:
            return {"status": error.category,
                    "message_zh_tw": "Token 探測有本機保護：每服務每日最多 10 次、每分鐘最多 1 次；未超額送出。"}
        try:
            from io import BytesIO

            from PIL import Image

            size = tuple(int(part) for part in canvas_size.split("x"))
            with Image.open(BytesIO(self._demo_cat_jpeg())) as source:
                image = source.convert("RGB").resize(size, Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
                jpeg = output.getvalue()
            offsets = tuple(-0.5 * (frame_count - 1 - index) for index in range(frame_count))
            board = TrackStoryboard("demo-cat", "track_01", jpeg, offsets, "local-demo")
            request = SemanticRequest(str(uuid4()), jpeg, ("demo-cat",), datetime.now(timezone.utc), (board,))
            started = time.monotonic()
            reply = provider.count_tokens(request, timeout_seconds=15)
            elapsed = round((time.monotonic() - started) * 1000)
            total = reply["totalTokens"]
            self.guard.finish_token_probe(probe_id, "completed",
                                          request_dispatched=getattr(reply, "request_dispatched", None),
                                          http_status=getattr(reply, "http_status", None),
                                          total_tokens=total, elapsed_ms=elapsed)
            return {"status": "completed", "http_status": reply.http_status,
                    "request_dispatched": reply.request_dispatched, "total_tokens": total,
                    "elapsed_ms": elapsed, "scope": "single_track_synthetic_demo",
                    "message_zh_tw": "這是單軌跡合成圖的 Gemini Token 數，不是實際家庭事件費用預測；實際事件會依貓數、裁切及供應商計量不同。"}
        except VisionFailure as error:
            self.guard.finish_token_probe(probe_id, error.category,
                                          request_dispatched=error.request_dispatched,
                                          http_status=error.http_status, elapsed_ms=error.api_elapsed_ms)
            return {"status": error.category, "http_status": error.http_status,
                    "request_dispatched": error.request_dispatched,
                    "message_zh_tw": "Gemini Token 數探測未完成；未保存服務原始回應或圖片，本機監控不受影響。"}
        except (OSError, RuntimeError, TypeError, ValueError):
            self.guard.finish_token_probe(probe_id, "unavailable", request_dispatched=False)
            return {"status": "unavailable", "request_dispatched": False,
                    "message_zh_tw": "Gemini Token 數探測未完成；未保存服務原始回應或圖片。"}

    def clear_key(self):
        with self._lock:
            values = self.read()
            if self._protector is not None:
                self._secret_path(values).unlink(missing_ok=True)
            else:
                try:
                    self._credential_manager().delete_vision_key(self.boundary(values))
                except CredentialStoreError as error:
                    if error.code not in {None, 1312}:
                        raise
                self._secret_path(values).unlink(missing_ok=True)

    def status(self):
        values = self.read()
        limits = self.quota_limits(values)
        credential_store_available = True
        try:
            key_configured = self._key_configured(values)
        except (OSError, RuntimeError, ValueError):
            credential_store_available = False
            key_configured = False
        return {"settings": values, "key_configured": key_configured,
                "credential_store_available": credential_store_available,
                "credential_backend": "test_only" if self._protector is not None else self._last_credential_backend,
                "capabilities":capabilities(values["provider_id"]),
                "policy": self.guard.status(self.boundary(values), **limits) if self.guard is not None else None,
                "quota_limits": limits,
                "health_check": self._read_health(),
                "restart_required": True, "experimental": True,
                "message_zh_tw": "設定只保存服務與同意資訊；Key 不回填。重新啟動服務後套用啟用狀態與等待秒數。"}

    def _read_health(self):
        """Return only the last safe metadata check; never persist provider bodies or secrets."""
        if not self.health_path.is_file():
            return {"status": "not_checked", "checked_at": None, "http_status": None}
        try:
            value = json.loads(self.health_path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or set(value) != {"status", "checked_at", "http_status"}
                    or value["status"] not in {"metadata_available", "model_not_confirmed", "unsupported_check",
                                               "auth_error", "rate_limited", "unavailable", "bad_response", "timeout"}
                    or not isinstance(value["checked_at"], str)
                    or (value["http_status"] is not None and type(value["http_status"]) is not int)):
                raise ValueError
            return value
        except (OSError, ValueError, TypeError):
            return {"status": "not_checked", "checked_at": None, "http_status": None}

    def _write_health(self, result):
        safe = {"status": result.get("status") if result.get("status") in {
                    "metadata_available", "model_not_confirmed", "unsupported_check", "auth_error",
                    "rate_limited", "unavailable", "bad_response", "timeout"} else "unavailable",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "http_status": result.get("http_status") if type(result.get("http_status")) is int else None}
        self.health_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.health_path.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(safe, file)
            os.replace(temporary, self.health_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def quota_limits(values):
        positive = [value for value in (values["tokens_per_minute_limit"], values["provider_tokens_per_minute_limit"])
                    if value > 0]
        daily = min(value for value in (values["daily_request_limit"], values["provider_daily_request_limit"])
                    if value > 0)
        rpm = min(value for value in (values["requests_per_minute_limit"], values["provider_requests_per_minute_limit"])
                  if value > 0)
        return {"daily_limit": daily, "rpm_limit": rpm, "tpm_limit": min(positive) if positive else 0,
                "estimated_tokens_per_request": values["estimated_tokens_per_request"],
                "reset_timezone": "America/Los_Angeles" if values["provider_id"] == "gemini" else "UTC"}

    def usage(self, *, hours=24, limit=100, offset=0):
        if type(hours) is not int or hours not in {1, 24, 168, 720}:
            raise ValueError("用量區間只允許最近1小時、24小時、7天或30天。")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("分析紀錄每頁只允許 1～200 筆。")
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise ValueError("分析紀錄頁面位置超出允許範圍。")
        values = self.read()
        if self.guard is None:
            return {"window_hours": hours, "sample_count": 0, "requests": 0, "buckets": [], "recent": [],
                    "recent_offset": offset, "recent_limit": limit, "recent_total": 0, "recent_has_more": False,
                    "message_zh_tw": "此執行環境沒有 SQLite 用量帳本；不補造分析紀錄。"}
        result = self.guard.usage(self.boundary(values), hours=hours,
                                  recent_limit=limit, recent_offset=offset)
        result["event_details"] = self._event_details(result.get("recent", []), values["provider_id"])
        return result

    @staticmethod
    def _parse_timeline_timestamp(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _parallel_timeline(cls, detail):
        """Build a bounded, timestamp-backed visualization without exposing delivery trace data."""
        analyses = detail.get("analysis") or {}
        telegram = detail.get("telegram") or {}
        analysis_times = analyses.get("timepoints", {})
        telegram_times = telegram.get("timepoints", {})
        lanes = []
        candidates = []

        def add_lane(label, entries):
            segments = []
            for start_key, end_key, segment_label, status in entries:
                start = cls._parse_timeline_timestamp(start_key)
                end = cls._parse_timeline_timestamp(end_key)
                if start is None or end is None or end <= start:
                    continue
                segments.append({"label": segment_label, "start": start, "end": end,
                                 "status": status if status in {"completed", "failed", "pending"} else "completed"})
                candidates.extend((start, end))
            if segments:
                lanes.append({"label": label, "segments": segments})

        analysis_status = "completed" if analyses.get("status") == "completed" else "failed"
        analysis_entries = [
            (analysis_times.get("queued_at"), analysis_times.get("started_at"), "排隊等待", "completed"),
            (analysis_times.get("started_at"), analysis_times.get("prepared_at"), "圖片準備", "completed"),
            (analysis_times.get("prepared_at") or analysis_times.get("started_at"),
             analysis_times.get("provider_started_at"), "分析工作準備", "completed"),
            (analysis_times.get("provider_started_at"), analysis_times.get("http_started_at"), "服務連線", "completed"),
            (analysis_times.get("http_started_at"), analysis_times.get("response_received_at"), "API 回應", analysis_status),
            (analysis_times.get("response_received_at"), analysis_times.get("finished_at"), "結果整理", analysis_status),
        ]
        if analysis_times.get("provider_started_at") and analysis_times.get("finished_at") \
                and not analysis_times.get("response_received_at"):
            analysis_entries.append((analysis_times.get("provider_started_at"), analysis_times.get("finished_at"),
                                     "服務等待／未取得回應", "failed"))
        add_lane("AI 分析", analysis_entries)
        wait_status = "completed" if telegram.get("wait_status") in {"completed", "candidate_empty", "not_requested"} else "failed"
        add_lane("影片與 Telegram", [
            (telegram_times.get("event_trigger_at"), telegram_times.get("media_sendable_at"), "影片準備", "completed"),
            (telegram_times.get("wait_started_at"), telegram_times.get("wait_finished_at"), "等待 AI 候選", wait_status),
        ])
        upload_segments = []
        for upload in telegram.get("uploads", [])[:8]:
            upload_segments.append((upload.get("started_at"), upload.get("finished_at"),
                                    f"Telegram 傳送 {upload.get('attempt', '?')}",
                                    "completed" if upload.get("accepted") else "failed"))
        add_lane("上傳回條", upload_segments)
        if not candidates:
            return {"lanes": [], "message_zh_tw": "此事件沒有足夠的階段時間戳，無法繪製真實並行時間軸。"}
        origin = min(candidates)
        finish = max(candidates)
        span_ms = max(1, min(7 * 24 * 60 * 60 * 1000, round((finish - origin).total_seconds() * 1000)))
        rendered_lanes = []
        for lane in lanes:
            segments = []
            for segment in lane["segments"]:
                start_ms = max(0, round((segment["start"] - origin).total_seconds() * 1000))
                end_ms = min(span_ms, round((segment["end"] - origin).total_seconds() * 1000))
                if end_ms <= start_ms:
                    continue
                segments.append({"label": segment["label"], "offset_ms": start_ms,
                                 "duration_ms": end_ms - start_ms, "status": segment["status"]})
            if segments:
                rendered_lanes.append({"label": lane["label"], "segments": segments})
        return {"origin_at": origin.isoformat(), "span_ms": span_ms, "lanes": rendered_lanes,
                "message_zh_tw": "時間軸依保存的 UTC 階段時間戳繪製；缺少的階段不補估。"}

    def _event_details(self, recent, provider_id):
        """Return a strict, bounded event summary; never expose media paths or recipient references."""
        if self.guard is None:
            return {}
        event_ids = list(dict.fromkeys(item.get("event_id") for item in recent
                                       if isinstance(item.get("event_id"), str) and item.get("event_id")))[:200]
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        store = self.guard.store
        with store._connection_lock:
            event_rows = store.connection.execute(
                f"SELECT event_id,event_type,trigger_at,pet_id,confidence,clip_status,telegram_status "
                f"FROM media_events WHERE event_id IN ({placeholders})", event_ids).fetchall()
            track_rows = store.connection.execute(
                f"SELECT event_id,track_id FROM media_event_tracks WHERE event_id IN ({placeholders}) "
                "ORDER BY event_id,track_id", event_ids).fetchall()
            semantic_rows = store.connection.execute(
                f"SELECT event_id,result_json,queued_at,provider_id,model_id,state FROM semantic_work_jobs "
                f"WHERE event_id IN ({placeholders}) AND provider_id=? ORDER BY rowid DESC LIMIT 800",
                [*event_ids, provider_id]).fetchall()
            delivery_rows = store.connection.execute(
                f"SELECT event_id,trace_json FROM event_delivery_traces WHERE event_id IN ({placeholders}) "
                "ORDER BY created_at DESC,rowid DESC LIMIT 800", event_ids).fetchall()
            identity_rows = store.connection.execute(
                f"SELECT e.event_id,m.track_id,i.note FROM media_events e "
                "JOIN media_event_tracks m ON m.event_id=e.event_id "
                "JOIN identity_inference_history i ON i.track_id=m.track_id "
                "JOIN behavior_events b ON b.event_id=i.event_id "
                f"WHERE e.event_id IN ({placeholders}) AND i.note LIKE '%identity_evidence_v1%' "
                "AND julianday(b.started_at)<=julianday(e.trigger_at)+e.requested_post_seconds/86400.0 "
                "AND (b.ended_at IS NULL OR julianday(b.ended_at)>=julianday(e.trigger_at)-e.requested_pre_seconds/86400.0) "
                "ORDER BY i.id DESC LIMIT 1600", event_ids).fetchall()
            pet_rows = store.connection.execute("SELECT pet_id,display_name FROM pets").fetchall()
            correction_rows = store.connection.execute(
                f"SELECT event_id,track_id,corrected_pet_id,confidence FROM identity_corrections "
                f"WHERE event_id IN ({placeholders}) ORDER BY id DESC LIMIT 1600", event_ids).fetchall()

        pets = {str(row["pet_id"]): str(row["display_name"])[:80] for row in pet_rows}
        events = {}
        for row in event_rows:
            pet_id = str(row["pet_id"] or "unknown")
            events[row["event_id"]] = {
                "event_type": str(row["event_type"]), "trigger_at": row["trigger_at"],
                "event_identity": {"status": "unknown" if pet_id == "unknown" else "recorded",
                                   "name": pets.get(pet_id, "未確認")},
                "confidence": round(float(row["confidence"]), 4),
                "clip_status": str(row["clip_status"]), "telegram_status": str(row["telegram_status"]),
                "tracks": [], "analysis": None, "telegram": None,
            }
        tracks = {}
        for row in track_rows:
            tracks.setdefault(row["event_id"], {}).setdefault(str(row["track_id"]), {
                "identity_candidates": [], "tags": []})
        for row in identity_rows:
            event = events.get(row["event_id"])
            track = tracks.get(row["event_id"], {}).get(str(row["track_id"]))
            if not event or not track or track["identity_candidates"]:
                continue
            try:
                evidence = json.loads(row["note"])
            except (TypeError, ValueError):
                continue
            candidates = evidence.get("candidates", []) if isinstance(evidence, dict) else []
            safe_candidates = []
            for candidate in candidates[:3] if isinstance(candidates, list) else []:
                if not isinstance(candidate, dict):
                    continue
                name = candidate.get("name")
                score = candidate.get("pet_score")
                if isinstance(name, str) and type(score) in {int, float} and 0 <= score <= 1:
                    safe_candidates.append({"name": name[:80], "score": round(float(score), 4), "experimental": True})
            if safe_candidates:
                track["identity_candidates"] = safe_candidates
        for row in correction_rows:
            track = tracks.get(row["event_id"], {}).get(str(row["track_id"]))
            name = pets.get(str(row["corrected_pet_id"] or ""))
            if track and name and not track["identity_candidates"]:
                track["identity_candidates"] = [{"name": name, "score": round(float(row["confidence"] or 0), 4),
                                                  "experimental": False, "source": "人工回報"}]
        for row in semantic_rows:
            event = events.get(row["event_id"])
            if event is None or event["analysis"] is not None:
                continue
            try:
                payload = json.loads(row["result_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            track_by_id = tracks.get(row["event_id"], {})
            for track_result in payload.get("tracks", [])[:20] if isinstance(payload.get("tracks"), list) else []:
                if not isinstance(track_result, dict):
                    continue
                track = track_by_id.get(str(track_result.get("track_id") or track_result.get("track_alias") or ""))
                if track is None:
                    continue
                tags = track_result.get("tags", [])
                if isinstance(tags, list):
                    track["tags"] = [
                        {"code": tag, "label": TAG_LABELS[tag], "candidate": True}
                        for tag in tags[:6] if isinstance(tag, str) and tag in TAG_LABELS
                    ]
            timing_values = payload.get("timings", {})
            allowed_timings = {"queue_ms", "preparation_ms", "provider_call_ms", "api_response_ms", "validation_ms"}
            timings = {key: round(float(value), 2) for key, value in timing_values.items()
                       if key in allowed_timings and type(value) in {int, float} and 0 <= value <= 86400000} \
                if isinstance(timing_values, dict) else {}
            timepoint_keys = {"queued_at", "started_at", "prepared_at", "provider_started_at",
                              "http_started_at", "response_received_at", "finished_at"}
            timepoints = {key: value for key, value in timing_values.items()
                          if key in timepoint_keys and self._parse_timeline_timestamp(value) is not None} \
                if isinstance(timing_values, dict) else {}
            event["analysis"] = {"status": str(payload.get("status") or row["state"] or "unknown"),
                                  "provider_id": str(row["provider_id"]), "model_id": str(row["model_id"]),
                                  "queued_at": row["queued_at"], "timings_ms": timings,
                                  "timepoints": timepoints,
                                  "identity_is_candidate_only": True}
        for row in delivery_rows:
            event = events.get(row["event_id"])
            if event is None or event["telegram"] is not None:
                continue
            try:
                trace = json.loads(row["trace_json"])
            except (TypeError, ValueError):
                continue
            uploads = trace.get("uploads", []) if isinstance(trace, dict) else []
            safe_uploads = [item for item in uploads if isinstance(item, dict) and type(item.get("accepted")) is bool]
            timepoint_keys = {"event_trigger_at", "media_sendable_at", "wait_started_at", "wait_finished_at"}
            timepoints = {key: value for key, value in trace.items()
                          if key in timepoint_keys and self._parse_timeline_timestamp(value) is not None}
            safe_uploads = [item for item in safe_uploads
                            if type(item.get("attempt")) is int and item["attempt"] > 0
                            and self._parse_timeline_timestamp(item.get("started_at")) is not None
                            and self._parse_timeline_timestamp(item.get("finished_at")) is not None]
            upload_attempts = len(safe_uploads)
            accepted_uploads = sum(item["accepted"] for item in safe_uploads)
            timeline_uploads = [{"attempt": item["attempt"], "started_at": item["started_at"],
                                 "finished_at": item["finished_at"], "accepted": item["accepted"]}
                                for item in safe_uploads[:8]]
            event["telegram"] = {
                "caption_source": trace.get("caption_source") if trace.get("caption_source") in {"local", "ai_candidate"} else "unknown",
                "wait_status": str(trace.get("wait_status") or "unknown"),
                "extra_wait_ms": trace.get("extra_wait_ms") if type(trace.get("extra_wait_ms")) in {int, float} else None,
                "wait_budget_ms": trace.get("wait_budget_ms") if type(trace.get("wait_budget_ms")) in {int, float} else None,
                "video_prepare_ms": trace.get("video_prepare_ms") if type(trace.get("video_prepare_ms")) in {int, float} else None,
                "transcode_ms": trace.get("validation_transcode_ms") if type(trace.get("validation_transcode_ms")) in {int, float} else None,
                "upload_attempts": upload_attempts, "accepted_uploads": accepted_uploads,
                "timepoints": timepoints, "uploads": timeline_uploads,
            }
        for event_id, event in events.items():
            event["tracks"] = list(tracks.get(event_id, {}).values())[:20]
            event["timeline"] = self._parallel_timeline(event)
        return events

    def check_connection(self):
        with self._lock:
            if time.monotonic()-self._last_health_check < 30:
                return {"status":"throttled", "message_zh_tw":"剛剛已檢查過，請等30秒；不重複發送探測。"}
            values = self.read()
            if not values["endpoint"] or not values["model_id"]:
                return {"status": "unavailable", "message_zh_tw": "目前保存的是未完成設定；請填完整服務網址與模型 ID，再檢查連線。未送出請求。"}
            settings = VisionEndpoint(values["endpoint"],values["model_id"])
            key = self.read_key()
            if urlsplit(values["endpoint"]).scheme == "https" and not key:
                result = {"status":"auth_error", "message_zh_tw":"請先安全保存目前服務的Key；未送出檢查。"}
                self._write_health(result)
                return result
            self._last_health_check = time.monotonic()
        result = health_check(values["provider_id"],settings,key,timeout_seconds=2)
        self._write_health(result)
        return result

    def build_provider(self):
        if not self.read()["enabled"]:
            return None
        owner = self
        initial = self.read()

        class ManagedProvider:
            provider_id = initial["provider_id"]
            capabilities = frozenset({"image"})

            model_id = initial["model_id"]
            config_reference = owner.boundary(initial)

            @property
            def storyboard_options(self):
                current = owner.read()
                return {"frame_count": current["storyboard_frame_count"],
                        "canvas_size": owner.effective_canvas_size(current),
                        "jpeg_quality": current["storyboard_jpeg_quality"],
                        "include_timestamps": current["storyboard_timestamps"],
                        "include_zones": current["storyboard_include_zones"],
                        "include_scene_references": current["storyboard_include_scene_references"]}

            def health_check(self, *, timeout_seconds=2):
                return owner.check_connection()

            def analyze(self, request, *, timeout_seconds):
                with owner._lock:
                    values = owner.read()
                    if not values["enabled"]:
                        raise VisionFailure("skipped_disabled")
                    if owner.boundary(values) != self.config_reference:
                        raise VisionFailure("skipped_disabled")
                    local = urlsplit(values["endpoint"]).scheme == "http"
                    if not local and not owner._key_configured(values):
                        raise VisionFailure("auth_error", request_dispatched=False)
                    key = owner.read_key()  # 同一設定快照，不能把新服務的 Key 送到舊網址。
                if not request.storyboards:
                    raise VisionFailure("insufficient_evidence")
                if owner.guard is None:
                    raise VisionFailure("unavailable", request_dispatched=False)
                reference = owner.boundary(values)
                limits = owner.quota_limits(values)
                job = owner.guard.reserve(reference, request, daily_limit=limits["daily_limit"],
                                          rpm_limit=limits["rpm_limit"], tpm_limit=limits["tpm_limit"],
                                          estimated_tokens=limits["estimated_tokens_per_request"] or None,
                                          model_id=values["model_id"], reset_timezone=limits["reset_timezone"],
                                          cooldown_seconds=values["event_merge_cooldown_seconds"],
                                          cooldown_ref=owner.event_cooldown_ref(reference, request.event_id))
                settings = VisionEndpoint(values["endpoint"], values["model_id"], enabled=True,
                                          upload_consent=True, json_object_supported=values["json_object_supported"],
                                          image_detail=owner.provider_image_detail(values))
                try:
                    result = PROVIDERS[values["provider_id"]](settings, lambda: key).analyze(request, timeout_seconds=timeout_seconds)
                except VisionFailure as error:
                    owner.guard.finish(job, error.category, retry_after=error.retry_after_seconds,
                                       request_dispatched=error.request_dispatched, http_status=error.http_status,
                                       api_elapsed_ms=error.api_elapsed_ms)
                    raise
                except (OSError, RuntimeError, TypeError, ValueError):
                    owner.guard.finish(job, "unavailable")
                    raise VisionFailure("unavailable") from None
                owner.guard.finish(job, "completed", request_dispatched=getattr(result, "request_dispatched", None),
                                   http_status=getattr(result, "http_status", None),
                                   api_elapsed_ms=getattr(result, "api_elapsed_ms", None),
                                   input_tokens=getattr(result, "input_tokens", None),
                                   output_tokens=getattr(result, "output_tokens", None))  # 僅記錄服務明確回傳的用量。
                return result
        return ManagedProvider()
