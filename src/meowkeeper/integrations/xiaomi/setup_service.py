"""Resumable Xiaomi Mi Home setup state machine.

The service never receives or persists a Xiaomi password, CAPTCHA, token or
full source URL.  Real discovery/profile probing is intentionally injectable:
without the user's browser login it remains ``waiting_for_login``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from meowkeeper.core.notifications.control_journal import CameraControlJournal, new_operation_id
from meowkeeper.core.notifications.privacy import PrivacyService
from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.go2rtc.process import Go2RtcProcess
from meowkeeper.integrations.go2rtc.redaction import mask_response_body, redact_diagnostic_text

from .camera_preferences import CameraPreferences
from .login_diagnostics import (
    XiaomiLoginDiagnostic,
    classify_login_response,
    safe_login_error_structure,
)
from .miot_power import MiotPowerClient
from .miss_privacy import MissPrivacyAdapter
from .models import XiaomiQualityProfile
from .quality_options import LIVE_PROFILE_OPTIONS, PROFILE_LABELS
from .quality_probe import choose_highest_stable_profile, probe_profile_names
from .relay_control import (
    RelayControlClient,
    RelayControlError,
    load_control_nonce,
    save_control_nonce,
)
from .relay_pairing import decode_pairing_code

SETUP_STAGES = (
    "not_installed",
    "downloading",
    "verifying",
    "ready",
    "starting",
    "waiting_for_login",
    "login_blocked",
    "verification_required",
    "captcha_required",
    "account_ready",
    "loading_devices",
    "device_selection_required",
    "probing_profiles",
    "stream_ready",
    "activating_source",
    "connected",
    "degraded",
    "failed",
)

# Live-view quality choices are deliberately narrower than the diagnostic
# probe matrix.  A user-facing switch must never turn into an unbounded
# profile sweep or a second camera connection.
DEFAULT_LIVE_PROFILE = "subtype=1"

# A MISS binary exposing operation=13 is not proof that the attached camera
# firmware moves to absolute coordinates. Keep this list empty until a model
# has a controlled real-device target/readback acceptance record.
VERIFIED_ABSOLUTE_MOVE_MODELS: frozenset[str] = frozenset()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mask(value: object, *, keep: int = 3) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    return text[:keep] + "***"


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    if not cleaned:
        raise ValueError("小米裝置代碼格式不正確。")
    return cleaned[:80]


def _epoch(value: object = None) -> float:
    if value is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(value, datetime):
        return value.timestamp()
    return float(value)


def _iso_from_epoch(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _epoch_from_iso(value: object) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


class XiaomiSetupService:
    def __init__(
        self,
        root: str | Path,
        *,
        go2rtc: Go2RtcProcess | None = None,
        state_path: str | Path | None = None,
        discovery: Callable[[], list[Mapping[str, Any]]] | None = None,
        profile_probe: Callable[[Mapping[str, Any], tuple[str, ...]], list[XiaomiQualityProfile]] | None = None,
        source_switch: Callable[[str, Mapping[str, Any], XiaomiQualityProfile], Mapping[str, Any]] | None = None,
        source_manager: Any | None = None,
        active_source_url: Callable[[], str | None] | None = None,
        active_source_frame_ready: Callable[[], bool] | None = None,
        login_cooldown_seconds: float = 300.0,
        experimental_ptz_enabled: bool = False,
        ptz_client: Any | None = None,
        view_change: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.view_change = view_change
        self.go2rtc = go2rtc or Go2RtcProcess(self.root)
        if state_path:
            self.state_path = Path(state_path).resolve()
        else:
            local_appdata = os.getenv("LOCALAPPDATA") if os.name == "nt" else None
            preferred_state = Path(local_appdata) / "MeowKeeperPlus" / "state" / "xiaomi-setup.json" if local_appdata else None
            self.state_path = preferred_state or self.root / "data" / "state" / "xiaomi-setup.json"
            if preferred_state is not None:
                try:
                    preferred_state.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    self.state_path = self.root / "data" / "state" / "xiaomi-setup.json"
        # AppData is the normal Windows runtime location.  A read-only or
        # policy-managed AppData file must not make discovery fail: the
        # public, secret-free setup state can fall back to the repository
        # runtime directory while the authenticated go2rtc session remains
        # untouched.  This is also useful for portable/restricted installs.
        self._state_fallback_path = self.root / "data" / "state" / "xiaomi-setup.json"
        self.control_journal = CameraControlJournal(
            self.state_path.with_name("xiaomi-control-operations.json"), limit=50,
        )
        self.discovery = discovery
        self.profile_probe = profile_probe
        self.source_switch = source_switch
        self.source_pause: Callable[[], Any] | None = None
        self.source_manager = source_manager
        self.active_source_url = active_source_url
        self.active_source_frame_ready = active_source_frame_ready
        self._selection_check_at = float("-inf")
        # Xiaomi MISS PTZ is implemented by an explicitly selected go2rtc
        # experimental build.  The official v1.9.14 binary does not expose
        # this endpoint, so the default is deliberately disabled.
        self.experimental_ptz_enabled = bool(experimental_ptz_enabled)
        self.ptz_client = ptz_client
        if login_cooldown_seconds < 0:
            raise ValueError("Xiaomi 登入冷卻秒數不可小於零。")
        self.login_cooldown_seconds = float(login_cooldown_seconds)
        self._devices: list[dict[str, Any]] = []
        self._device_records: dict[str, dict[str, Any]] = {}
        self._device_aliases: dict[str, str] = {}
        self._profiles: dict[str, list[XiaomiQualityProfile]] = {}
        self._camera_control_lock = threading.Lock()
        from .connection_jobs import ConnectionJobs
        self.connection_jobs = ConnectionJobs(self)
        self._state = self._load_state()
        self._relay_pairs_path = self.state_path.with_name("xiaomi-remote-pairs.json")
        self.camera_preferences = CameraPreferences(self.state_path.with_name("xiaomi-camera-preferences.json"))
        self._relay_pairs = self._load_relay_pairs()
        self._relay_control_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._relay_control_pending: set[str] = set()
        self._relay_control_lock = threading.Lock()
        from .relay_provider import RelayProvider
        self.relay_provider = RelayProvider(self)
        self.miot_power = MiotPowerClient(self.root, getattr(self.go2rtc, "config_path", self.root / "data/go2rtc/config.yaml"),
                                         Go2RtcClient(self.go2rtc.api_url, timeout_seconds=15)
                                         if isinstance(self.go2rtc, Go2RtcProcess) else None)
        self.privacy_adapter = MissPrivacyAdapter(
            self.state_path.with_name("xiaomi-private-pose.json"),
            device=self._privacy_device,
            read=lambda device: self.ptz_status(device, refresh=True),
            move=lambda device, angle, elevation: self.ptz_action(
                device, action="position", angle=angle, elevation=elevation,
            ),
            power=self._native_power,
        )
        self.privacy_service = PrivacyService(self.privacy_adapter, journal=self.control_journal)

    def _native_power(self, device_id: str, on: bool | None) -> Mapping[str, Any]:
        resolved = self._resolve_device(device_id)
        if resolved is None or device_id != self._privacy_device():
            return {"success": False}
        # Cloud MIoT is independent of the video relay. The helper validates
        # DID/model against this account's same-region fresh device list.
        source = str(resolved[1].get("source_url") or "")
        return self.miot_power.request(source, on=on)

    def _privacy_device(self) -> str | None:
        return next((str(item["internal_id"]) for item in self.devices()
                     if item.get("selected") and item.get("internal_id")), None)

    def privacy_action(self, device_id: str, action: str, *, mode: str | None = None) -> dict[str, Any]:
        if device_id != self._privacy_device():
            return {"success": False, "message_zh_tw": "只能控制目前已接入的單一攝影機。"}
        if action in {"capture", "set_mode"}:
            try:
                return (self.privacy_adapter.set_mode(str(mode or "")) if action == "set_mode"
                        else self.privacy_adapter.capture_private_pose())
            except (OSError, ValueError, TypeError, RuntimeError):
                return {"success": False, "message_zh_tw": "隱私位置無法保存；請確認 MISS 位置讀回與原位置恢復狀態。"}
        if action in {"hide", "show"}:
            if self.view_change is not None:
                self.view_change("privacy")
            return (self.privacy_service.hide(camera_id=device_id) if action == "hide"
                    else self.privacy_service.show(camera_id=device_id))
        return {"success": False, "message_zh_tw": "不支援的隱私操作。"}

    def privacy_configuration(self, device_id: str) -> dict[str, Any]:
        if device_id != self._privacy_device():
            raise ValueError("只能讀取目前已接入攝影機的隱私設定。")
        return self.privacy_adapter.configuration()

    def control_diagnostics(self, operation_id: str | None = None) -> dict[str, Any]:
        return self.control_journal.recent(operation_id)

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.is_file():
            try:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("setup_stage") in SETUP_STAGES:
                    return self._public_state(payload)
            except (OSError, json.JSONDecodeError):
                pass
        return self._public_state({"setup_stage": "not_installed", "retry_count": 0})

    @staticmethod
    def _public_state(payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "setup_stage", "selected_region", "selected_account_id_masked", "selected_device_id_masked",
            "selected_model", "selected_profile", "latest_error_code", "retry_count", "updated_at",
            "selected_stream_name",
            "profile_error_message",
            "login_classification", "login_http_status", "login_content_type", "login_message_zh_tw",
            "login_cooldown_until", "login_attempt_count", "login_last_attempt_at", "login_diagnostic",
            "connection_modes",
        }
        state = {key: payload.get(key) for key in allowed}
        state["setup_stage"] = state.get("setup_stage") if state.get("setup_stage") in SETUP_STAGES else "not_installed"
        state["retry_count"] = int(state.get("retry_count") or 0)
        state["login_attempt_count"] = int(state.get("login_attempt_count") or 0)
        modes = state.get("connection_modes")
        state["connection_modes"] = {str(k): v for k, v in list(modes.items())[:256]
                                     if v in {"local", "relay"} and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(k))} if isinstance(modes, dict) else {}
        diagnostic = state.get("login_diagnostic")
        if isinstance(diagnostic, Mapping):
            safe_diagnostic = dict(diagnostic)
            if "description" in safe_diagnostic:
                safe_diagnostic["description"] = redact_diagnostic_text(safe_diagnostic["description"], limit=240)
            if "response_body_masked" in safe_diagnostic:
                safe_diagnostic["response_body_masked"] = mask_response_body(safe_diagnostic["response_body_masked"])
            if "stderr_masked" in safe_diagnostic:
                safe_diagnostic["stderr_masked"] = redact_diagnostic_text(safe_diagnostic["stderr_masked"])
            safe_diagnostic["login_error"] = safe_login_error_structure(safe_diagnostic.get("login_error"))
            state["login_diagnostic"] = safe_diagnostic
        else:
            state["login_diagnostic"] = None
        state["updated_at"] = state.get("updated_at") or _now()
        return state

    def _save(self, **updates: Any) -> dict[str, Any]:
        self._state = self._public_state({**self._state, **updates, "updated_at": _now()})
        payload = json.dumps(self._state, ensure_ascii=False, indent=2) + "\n"
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(payload, encoding="utf-8")
        except OSError:
            if self.state_path.resolve() == self._state_fallback_path.resolve():
                raise
            self.state_path = self._state_fallback_path
            self._relay_pairs_path = self.state_path.with_name("xiaomi-remote-pairs.json")
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(payload, encoding="utf-8")
        return self.status()

    def _load_relay_pairs(self) -> dict[str, dict[str, str | int]]:
        try:
            payload = json.loads(self._relay_pairs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return {}
        pairs = payload.get("pairs")
        return pairs if isinstance(pairs, dict) else {}

    def _save_relay_pairs(self) -> None:
        # Never save the one-time code, Xiaomi DID, account, token, or source URL.
        import tempfile

        target = self._relay_pairs_path
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=".xiaomi-remote-pairs-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as output:
                json.dump({"version": 1, "pairs": self._relay_pairs}, output, ensure_ascii=False, indent=2)
                output.write("\n")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _probe_remote_jpeg(url: str) -> bool:
        from meowkeeper.runtime import bundled_binary

        ffmpeg = bundled_binary("ffmpeg")
        if not ffmpeg:
            return False
        try:
            result = subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
                 "-i", url, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                capture_output=True, timeout=25, check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        frame = result.stdout
        return result.returncode == 0 and 4 <= len(frame) <= 2 * 1024 * 1024 and frame.startswith(b"\xff\xd8") and frame.endswith(b"\xff\xd9")

    def pair_remote(self, device_id: str, code: str) -> dict[str, Any]:
        resolved = self._resolve_device(device_id)
        if resolved is None:
            return {"success": False, "message_zh_tw": "請先重新載入小米裝置，再配對遠端中繼。"}
        internal_id, device = resolved
        try:
            claim = decode_pairing_code(code)
        except ValueError as exc:
            return {"success": False, "message_zh_tw": str(exc)}
        did = str(device.get("device_id") or device.get("did") or "")
        model = str(device.get("model") or "")
        if not did or not claim.matches_device(did=did, model=model):
            return {"success": False, "message_zh_tw": "配對碼與此小米裝置不符；請確認在遠端電腦選的是同一台攝影機。"}
        region = str(device.get("region") or "")
        if region not in {"unconfirmed", "未知區域", claim.region}:
            return {"success": False, "message_zh_tw": "配對碼的後端區域與此裝置不符。"}
        if not self._probe_remote_jpeg(claim.rtsp_url):
            return {"success": False, "message_zh_tw": "此電腦尚未從遠端中繼取得真實影格；未保存配對。"}
        previous = self._relay_pairs.get(internal_id)
        try:
            save_control_nonce(self._relay_control_path(internal_id), claim.nonce)
        except (OSError, RelayControlError):
            return {"success": False, "message_zh_tw": "遠端影格已驗證，但此電腦無法安全保存控制配對碼；未保存配對。"}
        self._relay_pairs[internal_id] = {
            "model": claim.model,
            "region": claim.region,
            "tailnet_ip": claim.tailnet_ip,
            "rtsp_port": claim.rtsp_port,
            "stream_name": claim.stream_name,
        }
        try:
            self._save_relay_pairs()
        except OSError:
            if previous is None:
                self._relay_pairs.pop(internal_id, None)
            else:
                self._relay_pairs[internal_id] = previous
            return {"success": False, "message_zh_tw": "遠端影格已驗證，但此電腦無法保存配對設定。"}
        return {"success": True, "message_zh_tw": "遠端中繼已配對並驗證影格；現在可在此裝置卡片按「接入遠端監控」。"}

    def _relay_control_path(self, internal_id: str) -> Path:
        key = hashlib.sha256(internal_id.encode("utf-8")).hexdigest()[:24]
        return self._relay_pairs_path.parent / f"xiaomi-relay-control-{key}.dpapi"

    def _relay_control_client(self, internal_id: str, device: Mapping[str, Any]) -> RelayControlClient:
        pair = self._relay_pairs.get(internal_id)
        if not isinstance(pair, dict) or pair.get("model") != device.get("model"):
            raise RelayControlError("此裝置尚未配對遠端控制通道。")
        nonce = load_control_nonce(self._relay_control_path(internal_id))
        return RelayControlClient(str(pair.get("tailnet_ip") or ""), nonce)

    def _relay_control_status(self, internal_id: str, device: Mapping[str, Any]) -> dict[str, Any]:
        with self._relay_control_lock:
            cached = self._relay_control_cache.get(internal_id)
            if (not cached or time.monotonic() - cached[0] >= 20) and internal_id not in self._relay_control_pending:
                self._relay_control_pending.add(internal_id)
                threading.Thread(
                    target=self._refresh_relay_control, args=(internal_id, dict(device)), daemon=True,
                    name="xiaomi-relay-control-status",
                ).start()
            return cached[1] if cached else {}

    def _refresh_relay_control(self, internal_id: str, device: Mapping[str, Any]) -> None:
        try:
            status = self._relay_control_client(internal_id, device).request("status")
        except RelayControlError:
            status = {"success": False, "quality_available": False, "ptz_available": False}
        with self._relay_control_lock:
            self._relay_control_cache[internal_id] = (time.monotonic(), status)
            self._relay_control_pending.discard(internal_id)

    def activate_remote(self, device_id: str) -> dict[str, Any]:
        if not self._camera_control_lock.acquire(blocking=False):
            return {"status": "busy", "success": False,
                    "message_zh_tw": "攝影機正在切換或接入；請等待本次操作完成。"}
        try:
            return self._activate_remote(device_id)
        finally:
            self._camera_control_lock.release()

    def _activate_remote(self, device_id: str) -> dict[str, Any]:
        resolved = self._resolve_device(device_id)
        if resolved is None or self.source_switch is None:
            return {"success": False, "message_zh_tw": "找不到已載入的小米裝置或來源切換功能。"}
        internal_id, device = resolved
        pair = self._relay_pairs.get(internal_id)
        if not isinstance(pair, dict) or pair.get("model") != device.get("model"):
            return {"success": False, "message_zh_tw": "此攝影機尚未配對遠端中繼。"}
        # Revalidate persisted endpoint through the same bounded parser; do
        # not allow an edited state file to turn this into arbitrary SSRF.
        from .relay_pairing import validated_relay_url

        try:
            url = validated_relay_url(
                address=str(pair["tailnet_ip"]), port=int(pair["rtsp_port"]), stream=str(pair["stream_name"]),
            )
        except (KeyError, ValueError, TypeError):
            return {"success": False, "message_zh_tw": "已保存的遠端中繼設定不合法；請重新配對。"}
        remembered = self.camera_preferences.get(f"remote:{internal_id}")
        profile_name = "remote"
        if remembered is not None:
            if remembered["stream_name"] != str(pair["stream_name"]):
                return {"success": False, "message_zh_tw": "遠端配對串流已變更；請確認新的配對與畫質，不沿用舊設定。"}
            try:
                restored = self._relay_control_client(internal_id, device).request(
                    "profile", profile=remembered["profile"],
                )
            except RelayControlError:
                restored = {"success": False}
            if restored.get("success") is not True or restored.get("video_verified") is not True:
                return {"success": False,
                        "message_zh_tw": "遠端上次畫質未恢復或未取得有效影格；保留原監控來源與已保存設定。"}
            profile_name = remembered["profile"]
        if not self._probe_remote_jpeg(url):
            return {"success": False, "message_zh_tw": "遠端中繼目前沒有可用影格；保留原監控來源，請檢查遠端電腦。"}
        try:
            result = self.source_switch(url, self._public_device(device), XiaomiQualityProfile(profile_name, None, None, None, None, None, True))
        except (OSError, RuntimeError, ValueError):
            return {"success": False, "message_zh_tw": "遠端來源切換失敗；原監控來源已嘗試恢復。"}
        if str(result.get("status")) not in {"ok", "connected", "running", "starting"}:
            return {"success": False, "message_zh_tw": "遠端來源切換失敗；原監控來源已嘗試恢復。"}
        self._save(setup_stage="activating_source", selected_model=device.get("model"), selected_profile=profile_name, selected_stream_name=str(pair["stream_name"]), profile_error_message=None)
        return {"success": True, "message_zh_tw": "遠端中繼已保存並開始監控；請在總覽確認即時影格、AI 與環形快取。"}

    def status(self) -> dict[str, Any]:
        message = self._state.get("login_message_zh_tw") or self._state.get("profile_error_message") or self._message(self._state["setup_stage"])
        return {
            **self._state,
            "go2rtc": self.go2rtc.status().to_dict(),
            "device_count": len(self._devices),
            "profile_count": sum(len(items) for items in self._profiles.values()),
            "login_retry": self.login_retry_status(),
            "message_zh_tw": message,
        }

    @staticmethod
    def _message(stage: str) -> str:
        return {
            "not_installed": "尚未準備 go2rtc。",
            "downloading": "正在取得官方 go2rtc。",
            "verifying": "正在驗證 go2rtc SHA-256。",
            "ready": "go2rtc 已準備完成。",
            "starting": "正在啟動 localhost go2rtc。",
            "waiting_for_login": "請在本機頁面完成 Xiaomi Mi Home 登入。",
            "login_blocked": "小米登入暫時封鎖；請依診斷完成官方驗證，冷卻後按明確重試。",
            "verification_required": "需要完成 Xiaomi 帳號驗證。",
            "captcha_required": "需要由使用者完成 CAPTCHA。",
            "account_ready": "帳號與裝置清單已載入；請確認攝影機區域與 SD 畫面。",
            "loading_devices": "正在載入 Xiaomi 裝置。",
            "device_selection_required": "請選擇要接入的攝影機。",
            "probing_profiles": "正在以 FFprobe 測試可用畫質。",
            "stream_ready": "已找到可持續的本機串流 profile。",
            "activating_source": "正在切換唯一 MeowKeeper+ 影音來源。",
            "connected": "小米攝影機已接入唯一影音來源。",
            "degraded": "小米來源目前降級或等待重新連線。",
            "failed": "小米設定流程失敗，請查看錯誤代碼。",
        }.get(stage, "小米設定流程尚未開始。")

    def login_retry_status(self, now: object = None) -> dict[str, Any]:
        """Return cooldown state without initiating a login request."""

        until = _epoch_from_iso(self._state.get("login_cooldown_until"))
        remaining = max(0, int((until - _epoch(now)) + 0.999)) if until is not None else 0
        blocked = self._state.get("setup_stage") == "login_blocked"
        return {
            "cooldown_active": remaining > 0,
            "retry_after_seconds": remaining,
            "cooldown_until": self._state.get("login_cooldown_until"),
            "explicit_retry_required": blocked or remaining > 0,
            "classification": self._state.get("login_classification"),
            "message_zh_tw": self._state.get("login_message_zh_tw"),
        }

    def can_attempt_login(self, *, explicit_retry: bool = False, now: object = None) -> bool:
        """Check whether a caller may open an explicit login attempt.

        This method never submits credentials and never performs an automatic
        retry.  A user action must pass ``explicit_retry=True`` after a failed
        Xiaomi login.
        """

        retry = self.login_retry_status(now)
        return explicit_retry or not retry["cooldown_active"]

    def record_login_diagnostic(
        self,
        diagnostic: XiaomiLoginDiagnostic | None = None,
        *,
        body: object = None,
        http_status: int | None = None,
        content_type: str | None = None,
        stderr: object = "",
        login_error: object = None,
        network_error: object = None,
        phase: str = "generic",
        expected_uid: str | None = None,
        now: object = None,
    ) -> dict[str, Any]:
        """Persist only the safe diagnostic result and apply login cooldown."""

        result = diagnostic or classify_login_response(
            body,
            http_status=http_status,
            content_type=content_type,
            stderr=stderr,
            login_error=login_error,
            network_error=network_error,
            phase=phase,
            expected_uid=expected_uid,
        )
        attempt_epoch = _epoch(now)
        cooldown_until = _iso_from_epoch(attempt_epoch + self.login_cooldown_seconds)
        stage = {
            "login_success": "account_ready" if result.phase == "get" else "waiting_for_login_verification",
            "login_failed": "login_blocked",
            "captcha_required": "captcha_required",
            "verification_required": "verification_required",
            "xiaomi_risk_control": "login_blocked",
            "invalid_credentials": "login_blocked",
            "unsupported_login_flow": "login_blocked",
            "upstream_api_changed": "login_blocked",
            "network_error": "degraded",
            "unknown_error": "login_blocked",
        }.get(result.classification, "login_blocked")
        payload = result.to_dict()
        cooldown_value = None if result.classification == "login_success" else cooldown_until
        saved = self._save(
            setup_stage=stage,
            latest_error_code=result.code,
            login_classification=result.classification,
            login_http_status=result.http_status,
            login_content_type=result.content_type,
            login_message_zh_tw=result.message_zh_tw,
            login_cooldown_until=cooldown_value,
            login_attempt_count=int(self._state.get("login_attempt_count") or 0) + 1,
            login_last_attempt_at=result.observed_at,
            login_diagnostic=payload,
        )
        saved["login_retry"] = self.login_retry_status(attempt_epoch)
        return saved

    def capture_go2rtc_login_diagnostic(self, client: Any, *, path: str = "/api/xiaomi", now: object = None) -> dict[str, Any]:
        """Capture localhost go2rtc evidence and classify it once.

        ``client`` is expected to be the read-only ``Go2RtcClient``.  The
        process log is read through its bounded redaction helper when
        available.  No login request, retry or credential submission occurs.
        """

        capture = getattr(client, "get_diagnostic", None)
        if not callable(capture):
            raise TypeError("Xiaomi 診斷需要支援 get_diagnostic() 的 localhost go2rtc client。")
        response = capture(path)
        stderr = ""
        read_log = getattr(self.go2rtc, "read_recent_stderr", None) or getattr(self.go2rtc, "read_recent_log", None)
        if callable(read_log):
            stderr = read_log()
        return self.record_login_diagnostic(
            body=getattr(response, "body_masked", ""),
            http_status=getattr(response, "http_status", None),
            content_type=getattr(response, "content_type", None),
            stderr=stderr,
            network_error=getattr(response, "transport_error", None),
            now=now,
        )

    def request_explicit_login_retry(self, *, now: object = None) -> dict[str, Any]:
        """Clear login cooldown after a deliberate user action.

        The method only re-opens the state-machine gate.  It never submits an
        account, password, CAPTCHA, verification code or token.
        """

        del now
        if self._state.get("setup_stage") not in {
            "login_blocked",
            "verification_required",
            "captcha_required",
            "degraded",
            "waiting_for_login",
        }:
            return {**self.status(), "retry_allowed": False, "message_zh_tw": "目前沒有可重試的小米登入失敗。"}
        return {
            **self._save(
                setup_stage="waiting_for_login",
                login_cooldown_until=None,
                login_message_zh_tw="已解除登入冷卻；請由官方登入頁完成驗證，再按重新載入裝置。",
            ),
            "retry_allowed": True,
        }

    def prepare(self) -> dict[str, Any]:
        # Do not rewrite config.yaml when the user already has an authenticated
        # official go2rtc session running outside this Host.  Rebuilding the
        # process here could clear the Xiaomi account/session unexpectedly.
        existing = self.go2rtc.status()
        if existing.healthy:
            self._save(setup_stage="waiting_for_login", latest_error_code=None, login_message_zh_tw=None)
            if self.discovery is not None:
                return self.refresh()
            return self.status()
        self._save(setup_stage="downloading", latest_error_code=None, login_message_zh_tw=None)
        installed = self.go2rtc.install()
        if not installed.installed:
            return self._save(setup_stage="failed", latest_error_code="GO2RTC_INSTALL_FAILED")
        self._save(setup_stage="starting")
        started = self.go2rtc.start()
        if not started.healthy:
            return self._save(setup_stage="failed", latest_error_code="GO2RTC_START_FAILED")
        return self._save(setup_stage="waiting_for_login")

    def open_login(self) -> dict[str, Any]:
        if self._state["setup_stage"] in {"not_installed", "failed"}:
            return {**self.status(), "status": "not_ready", "message_zh_tw": "請先準備官方 go2rtc。"}
        retry = self.login_retry_status()
        if retry["cooldown_active"]:
            return {
                **self.status(),
                "status": "cooldown_active",
                "retry_allowed": False,
                "message_zh_tw": f"登入暫停中，請等待 {retry['retry_after_seconds']} 秒後按明確重試。",
            }
        url = self.go2rtc.api_url + "/"  # localhost only; no credentials or tokens in URL.
        opened = bool(webbrowser.open(url))
        return {**self.status(), "login_url": url, "browser_opened": opened}

    def refresh(self) -> dict[str, Any]:
        retry = self.login_retry_status()
        if retry["cooldown_active"]:
            return {**self.status(), "status": "cooldown_active", "message_zh_tw": f"登入暫停中，請等待 {retry['retry_after_seconds']} 秒後按明確重試。"}
        if self.discovery is None:
            return self.status()
        active_local_stream = self._active_local_stream_name()
        saved_stream = str(self._state.get("selected_stream_name") or "").strip()
        saved_selection_matches_source = bool(active_local_stream and active_local_stream == saved_stream)
        refresh_updates: dict[str, Any] = {
            "setup_stage": "loading_devices",
            "profile_error_message": None,
            "latest_error_code": None,
        }
        # A plain device refresh must not display a profile/device selection
        # left over from an earlier failed process.  Preserve an active local
        # stream when the user is only refreshing the account list.
        if self._state.get("setup_stage") != "connected" and not saved_selection_matches_source:
            refresh_updates.update(
                selected_device_id_masked=None,
                selected_model=None,
                selected_profile=None,
                selected_stream_name=None,
            )
        self._save(**refresh_updates)
        try:
            raw = self.discovery()
            self._devices = []
            self._device_records.clear()
            self._device_aliases.clear()
            self._profiles.clear()
            ordered_items = sorted(
                raw,
                key=lambda item: (
                    # The requested Standard 2K target receives the stable
                    # first local alias regardless of source-list ordering.
                    0 if str(item.get("model") or "") == "isa.camera.hlc7" else 1,
                    str(item.get("display_name") or item.get("name") or ""),
                ),
            )
            model_counts: dict[str, int] = {}
            for item in ordered_items:
                model = str(item.get("model") or "")
                model_counts[model] = model_counts.get(model, 0) + 1
            for index, item in enumerate(ordered_items, start=1):
                record = dict(item)
                record.setdefault("stream_name", f"xiaomi_camera_{index}")
                internal_id = str(record.get("internal_id") or "").strip()
                if not internal_id:
                    source = str(record.get("source_url") or record.get("device_id") or record.get("did") or "")
                    label = str(record.get("display_name") or record.get("name") or "xiaomi")
                    internal_id = "xiaomi_camera_" + hashlib.sha256(f"{label}\x00{source}".encode()).hexdigest()[:10]
                    record["internal_id"] = internal_id
                remembered = self.camera_preferences.get(internal_id)
                if remembered is not None:
                    record["stream_name"] = remembered["stream_name"]
                elif (
                    model_counts.get(str(record.get("model") or "")) == 1
                    and record.get("model") == self._state.get("selected_model")
                    and saved_stream
                ):
                    # Older releases stored the selected profile/stream only
                    # in setup state. Reuse it for a unique model; the active
                    # go2rtc producer below still confirms device identity.
                    record["stream_name"] = saved_stream
                self._device_records[internal_id] = record
                for alias in (record.get("device_id"), record.get("did"), internal_id):
                    if alias:
                        self._device_aliases[str(alias)] = internal_id
                self._devices.append(self._public_device(record))
            restored_local = self._restore_active_local_selection()
        except Exception:  # noqa: BLE001 - external account state is optional
            return self._save(setup_stage="failed", latest_error_code="XIAOMI_DISCOVERY_FAILED", retry_count=self._state["retry_count"] + 1)
        if restored_local:
            # Preserve the recovered state instead of overwriting it with
            # account_ready at the end of refresh.
            return self.status()
        if any(self._remote_active(str(item.get("internal_id") or ""), item) for item in self._devices):
            return self._save(setup_stage="connected", latest_error_code=None, profile_error_message=None)
        if saved_selection_matches_source:
            # The saved source still points at this stream, but its producer
            # may be warming up. Keep the binding for a user-initiated retry.
            return self._save(setup_stage="activating_source")
        return self._save(
            setup_stage="account_ready" if self._devices else "device_selection_required",
            selected_device_id_masked=None,
            selected_model=None,
            selected_profile=None,
            selected_stream_name=None,
        )

    def _active_local_stream_name(self) -> str | None:
        if self.active_source_url is None:
            return None
        try:
            source = urlsplit(str(self.active_source_url() or ""))
            if (
                source.scheme != "rtsp"
                or source.hostname != "127.0.0.1"
                or source.port != self.go2rtc.rtsp_port
                or source.username is not None
                or source.password is not None
                or source.query
                or source.fragment
            ):
                return None
            stream_name = source.path.removeprefix("/")
            return stream_name if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", stream_name) else None
        except (ValueError, TypeError):
            return None

    def native_snapshot_jpeg(self) -> bytes | None:
        """Return one native-resolution frame from the active local stream.

        The request is deliberately limited to the already selected local
        Xiaomi stream.  It never accepts or reconstructs a ``xiaomi://`` URL,
        and therefore reuses the existing go2rtc producer instead of opening
        another camera connection.  Remote relay sources are left to their
        relay-specific snapshot path and return ``None`` here.
        """

        stream_name = self._active_local_stream_name()
        selected = str(self._state.get("selected_stream_name") or "").strip()
        if not stream_name or stream_name != selected:
            return None
        client = self._get_ptz_client()
        read_frame = getattr(client, "get_frame_jpeg", None)
        if not callable(read_frame):
            return None
        try:
            return read_frame(stream_name, timeout_seconds=12.0)
        except (OSError, RuntimeError, ValueError):
            return None

    def _restore_active_local_selection(self) -> bool:
        """Recover the selected device only from the actual local RTSP producer.

        The saved wizard stage can describe a failed probe of another camera.
        Neither that stage nor a matching stream name alone proves which device
        the currently running Host is displaying.
        """

        if self.active_source_url is None or self.source_manager is None:
            return False
        try:
            stream_name = self._active_local_stream_name()
            if not stream_name:
                return False
            client = getattr(self.source_manager, "client", None)
            read_status = getattr(client, "stream_status", None)
            if not callable(read_status):
                return False
            status = read_status(stream_name)
            producers = status.get("producers") if isinstance(status, Mapping) else None
            if not isinstance(producers, list):
                return False
            candidates = [
                item for item in self._devices
                if item.get("model")
                and any(
                    isinstance(producer, Mapping)
                    and producer.get("format_name") == "xiaomi/miss"
                    and producer.get("user_agent") == f"CS2 ({item['model']})"
                    for producer in producers
                )
            ]
            if not candidates:
                return False
            preference_matches = [
                item for item in candidates
                if (self.camera_preferences.get(str(item.get("internal_id") or "")) or {}).get("stream_name") == stream_name
            ]
            saved_state_matches = [
                item for item in candidates
                if item.get("model") == self._state.get("selected_model")
                and self._state.get("selected_stream_name") == stream_name
            ]
            matches = preference_matches if len(preference_matches) == 1 else (
                saved_state_matches if len(saved_state_matches) == 1 else candidates
            )
            if len(matches) != 1:
                # Identical camera models cannot be distinguished by the
                # go2rtc user-agent alone. Never attach controls ambiguously.
                return False
            selected_device = matches[0]
            selected_id = str(selected_device.get("internal_id") or "")
            model = str(selected_device.get("model") or "")
            preference = self.camera_preferences.get(selected_id)
            selected_profile = None
            if preference and preference.get("stream_name") == stream_name:
                selected_profile = preference.get("profile")
            elif (
                model == self._state.get("selected_model")
                and stream_name == self._state.get("selected_stream_name")
                and self._state.get("selected_profile") in LIVE_PROFILE_OPTIONS
            ):
                selected_profile = self._state.get("selected_profile")
            self._devices = [
                {
                    **item,
                    "selected": item.get("internal_id") == selected_id,
                    "stream_name": stream_name if item.get("internal_id") == selected_id else item.get("stream_name", ""),
                }
                for item in self._devices
            ]
            record = self._device_records.get(selected_id)
            if record is not None:
                record["stream_name"] = stream_name
            self._save(
                setup_stage="connected",
                selected_device_id_masked=_mask(selected_id),
                selected_model=model,
                selected_profile=selected_profile,
                selected_stream_name=stream_name,
                latest_error_code=None,
                profile_error_message=None,
            )
            return True
        except (OSError, RuntimeError, ValueError, TypeError):
            # A transient go2rtc status failure must not break device listing.
            return False

    def _remembered_preference(self, internal_id: str, device: Mapping[str, Any]) -> dict[str, Any] | None:
        """Read per-camera resume settings, including the legacy setup record."""

        remembered = self.camera_preferences.get(internal_id)
        if remembered is not None:
            return remembered
        stream_name = str(device.get("stream_name") or "")
        profile = self._state.get("selected_profile")
        if (
            device.get("model") == self._state.get("selected_model")
            and stream_name
            and stream_name == self._state.get("selected_stream_name")
            and profile in LIVE_PROFILE_OPTIONS
        ):
            return {"profile": str(profile), "stream_name": stream_name}
        return None

    @staticmethod
    def _public_device(device: Mapping[str, Any]) -> dict[str, Any]:
        internal_id = str(device.get("internal_id") or "")[:80]
        if not internal_id:
            source = str(device.get("source_url") or device.get("device_id") or "")
            internal_id = "xiaomi_camera_" + hashlib.sha256(source.encode()).hexdigest()[:10]
        available_profiles = device.get("available_profiles")
        if not isinstance(available_profiles, list):
            available_profiles = list(probe_profile_names())
        masked_ip = str(device.get("masked_ip") or _mask(device.get("lan_ip") or device.get("ip"), keep=7) or "未取得")[:40]
        return {
            "internal_id": internal_id,
            "device_id": _mask(device.get("device_id") or device.get("did")),
            "display_name": str(device.get("display_name") or device.get("name") or "未命名裝置")[:120],
            "model": str(device.get("model") or "未知型號")[:120],
            "region": str(device.get("region") or "未知區域")[:40],
            "masked_ip": masked_ip,
            "lan_ip": masked_ip,
            "firmware_version": str(device.get("firmware_version") or "尚未取得")[:80],
            "online_status": str(device.get("online_status") or "unknown")[:40],
            "supported_status": str(device.get("supported_status") or "candidate")[:40],
            "available_profiles": [str(item)[:40] for item in available_profiles[:12]],
            "selected": bool(device.get("selected")),
            "source_reference_present": bool(device.get("source_reference_present") or device.get("source_url")),
            "stream_name": str(device.get("stream_name") or "")[:80],
        }

    def devices(self) -> list[dict[str, Any]]:
        self._reconcile_active_selection()
        result: list[dict[str, Any]] = []
        for item in self._devices:
            internal_id = str(item.get("internal_id") or "")
            item = {**item, "connection_mode": self._state.get("connection_modes", {}).get(internal_id, "local")}
            remembered = self._remembered_preference(internal_id, item)
            pair = self._relay_pairs.get(internal_id)
            remote_url = None
            if isinstance(pair, dict):
                try:
                    from .relay_pairing import validated_relay_url

                    remote_url = validated_relay_url(
                        address=str(pair["tailnet_ip"]), port=int(pair["rtsp_port"]), stream=str(pair["stream_name"]),
                    )
                except (KeyError, TypeError, ValueError):
                    remote_url = None
            remote_active = self._remote_active(internal_id, item)
            if remote_active:
                remembered = self.camera_preferences.get(f"remote:{internal_id}") or remembered
            remote_control = self._relay_control_status(internal_id, item) if remote_active else {}
            profile = choose_highest_stable_profile(self._profiles.get(internal_id, []))
            quality = None
            if profile is not None:
                quality = {
                    "name": profile.name,
                    "width": profile.width,
                    "height": profile.height,
                    "fps": profile.fps,
                    "video_codec": profile.video_codec,
                    "audio_codec": profile.audio_codec,
                }
                saved_metadata = remembered.get("metadata") if isinstance(remembered, Mapping) else None
                if (isinstance(saved_metadata, Mapping)
                        and remembered.get("profile") == profile.name
                        and remembered.get("stream_name") == item.get("stream_name")):
                    if isinstance(saved_metadata.get("verified_at"), str):
                        quality["verified_at"] = saved_metadata["verified_at"]
                    quality["historical"] = False
            elif remembered and remembered.get("metadata"):
                quality = {"name": remembered["profile"], **remembered["metadata"], "historical": True}
            selected_live = remote_active or bool(item.get("selected")) and (
                self.active_source_url is None
                or self._active_local_stream_name() == item.get("stream_name")
            )
            selected_profile = (
                str(self._state.get("selected_profile") or DEFAULT_LIVE_PROFILE)
                if selected_live
                else DEFAULT_LIVE_PROFILE
            )
            result.append(
                {
                    **item,
                    "selected": selected_live,
                    "monitoring_active": bool(selected_live and self._active_frame_ready()),
                    "connection_mode": "remote_relay" if remote_active else "local_go2rtc",
                    "preferred_connection_mode": self._state.get("connection_modes", {}).get(internal_id, "local"),
                    "remote_relay_paired": bool(remote_url),
                    "profile_ready": profile is not None,
                    "remembered_profile": (remembered or {}).get("profile"),
                    "resume_available": bool(remembered) and callable(
                        getattr(self.source_manager, "verify_active_profile", None)
                    ),
                    "quality": quality,
                    "quality_profiles": [
                        {"value": name, "label": PROFILE_LABELS[name]}
                        for name in LIVE_PROFILE_OPTIONS
                    ],
                    "quality_switch_available": bool(selected_live and (not remote_active or remote_control.get("quality_available"))),
                    "quality_switch_reason": (
                        "遠端控制通道尚未通過驗證；影像仍可觀看，請確認遠端控制程序與防火牆。"
                        if remote_active and not remote_control.get("quality_available")
                        else "可切換遠端中繼畫質；只使用既有單一 stream，並於切換後驗證真實影格。"
                        if remote_active else "請先接入這台攝影機，才能切換即時畫面畫質。"
                        if not selected_live else "可切換目前本機 Xiaomi 來源的畫質。"
                    ),
                    "selected_profile": selected_profile,
                    "ptz": {
                        "enabled": bool(remote_control.get("ptz_available")) if remote_active else self.experimental_ptz_enabled,
                        "status": ("remote_relay_ready" if remote_control.get("ptz_available") else "remote_relay_unavailable") if remote_active else ("experimental_ready" if self.experimental_ptz_enabled else "official_binary_only"),
                        "message_zh_tw": (
                            "遠端 PTZ 控制通道尚未通過驗證；未執行鏡頭操作。"
                            if remote_active else "已明確啟用 Xiaomi MISS PTZ 實驗介面；只有活動中的 Xiaomi producer 才能控制。"
                            if self.experimental_ptz_enabled
                            else "目前使用官方 go2rtc；Xiaomi MISS PTZ 實驗版尚未啟用。"
                        ),
                    },
                }
            )
        return result

    def _active_frame_ready(self) -> bool:
        try:
            return bool(self.active_source_frame_ready and self.active_source_frame_ready())
        except (OSError, RuntimeError, ValueError):
            return False

    def _reconcile_active_selection(self) -> None:
        """Retry read-only binding after producer warm-up or a failed probe."""

        stream = self._active_local_stream_name()
        if not stream or not self._devices:
            return
        if (
            self._state.get("setup_stage") == "connected"
            and self._state.get("selected_stream_name") == stream
            and any(item.get("selected") and item.get("stream_name") == stream for item in self._devices)
        ):
            return
        now = time.monotonic()
        if now - self._selection_check_at < 5 or not self._camera_control_lock.acquire(blocking=False):
            return
        try:
            self._selection_check_at = now
            self._restore_active_local_selection()
        finally:
            self._camera_control_lock.release()

    def _remote_active(self, internal_id: str, device: Mapping[str, Any]) -> bool:
        """Match only a validated saved relay to the currently active source."""

        pair = self._relay_pairs.get(internal_id)
        if not isinstance(pair, dict) or pair.get("model") != device.get("model") or self.active_source_url is None:
            return False
        try:
            from .relay_pairing import validated_relay_url

            url = validated_relay_url(
                address=str(pair["tailnet_ip"]), port=int(pair["rtsp_port"]),
                stream=str(pair["stream_name"]),
            )
            return self.active_source_url() == url
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            return False

    @staticmethod
    def _live_profile(name: object) -> str:
        value = str(name or "").strip().lower()
        if value == "auto":
            value = "subtype=auto"
        if not value.startswith("subtype="):
            value = f"subtype={value}"
        if value not in LIVE_PROFILE_OPTIONS:
            raise ValueError("即時畫面只支援 subtype=sd、hd、auto 或 0～5。")
        return value

    def set_live_profile(self, device_id: str, profile_name: str) -> dict[str, Any]:
        self._reconcile_active_selection()
        if not self._camera_control_lock.acquire(blocking=False):
            return {"status": "busy", "success": False,
                    "message_zh_tw": "攝影機正在切換或接入；請等待本次操作完成。"}
        try:
            if self.view_change is not None:
                self.view_change("profile_change")
            return self._set_live_profile(device_id, profile_name)
        finally:
            self._camera_control_lock.release()

    def _set_live_profile(self, device_id: str, profile_name: str) -> dict[str, Any]:
        """Replace the active local stream profile without adding a producer."""

        try:
            selected_name = self._live_profile(profile_name)
        except ValueError as exc:
            return {"status": "invalid", "success": False, "message_zh_tw": str(exc)}
        resolved = self._resolve_device(device_id)
        if resolved is None:
            return {"status": "not_found", "success": False, "message_zh_tw": "找不到已載入的 Xiaomi 裝置。"}
        internal_id, device = resolved
        if self._remote_active(internal_id, device):
            try:
                result = self._relay_control_client(internal_id, device).request("profile", profile=selected_name)
            except RelayControlError:
                result = {"success": False}
            with self._relay_control_lock:
                self._relay_control_cache.pop(internal_id, None)
            if result.get("success") is True and result.get("video_verified") is True:
                pair = self._relay_pairs[internal_id]
                # Local and relay stream names are different namespaces; a
                # remote change must not overwrite local resume settings.
                self.camera_preferences.remember(f"remote:{internal_id}", selected_name, str(pair["stream_name"]),
                                                 metadata=result.get("source_metadata"))
                self._save(selected_profile=selected_name)
                return {"status": "ok", "success": True, "profile": selected_name, "video_verified": True,
                        "message_zh_tw": "遠端中繼已切換畫質並重新驗證真實影格。"}
            return {"status": "remote_control_unavailable", "success": False,
                    "message_zh_tw": "遠端畫質切換未取得真實影格或控制通道不可用；未確認畫質已改變。"}
        stream_name = self._ptz_stream(device)
        if not stream_name:
            return {
                "status": "not_ready",
                "success": False,
                "message_zh_tw": "目前沒有可切換的活動 Xiaomi 來源；請先完成接入。",
            }
        manager_activate = getattr(self.source_manager, "activate_stream", None) if self.source_manager is not None else None
        if not callable(manager_activate):
            return {"status": "unavailable", "success": False, "message_zh_tw": "Xiaomi 來源管理器尚未接線。"}
        profile = XiaomiQualityProfile(selected_name, None, None, None, None, None, False)
        previous_name = str((self._remembered_preference(internal_id, device) or {}).get("profile")
                            or self._state.get("selected_profile") or DEFAULT_LIVE_PROFILE)
        verifier = getattr(self.source_manager, "verify_active_profile", None)
        if not callable(verifier):
            return {"status": "unavailable", "success": False,
                    "message_zh_tw": "畫質影格驗證尚未接線；未變更目前來源。"}
        if self.source_switch is None:
            return {"status": "unavailable", "success": False,
                    "message_zh_tw": "主要監控重連尚未接線；未變更目前來源。"}
        try:
            # Release the old consumer before PUT replaces the Stream object.
            if self.source_pause is not None:
                self.source_pause()
            manager_activate(internal_id, stream_name, profile)
            verified = verifier(stream_name, selected_name)
            if not isinstance(verified, XiaomiQualityProfile) or not verified.stable:
                raise RuntimeError("畫質未取得有效影格。")
            # PUT creates a new go2rtc Stream object. The old RTSP consumer
            # otherwise keeps showing video while PTZ sees an idle new object.
            if self.source_switch is None:
                raise RuntimeError("畫質切換後的監控重連尚未接線。")
            switched = self.source_switch(
                f"rtsp://127.0.0.1:{self.go2rtc.rtsp_port}/{stream_name}",
                self._public_device(device), verified,
            )
            if not isinstance(switched, Mapping) or switched.get("status") not in {"running", "connected", "ok"}:
                raise RuntimeError("畫質已驗證但主要監控尚未重連。")
        except (OSError, RuntimeError, ValueError):
            rollback_ok = False
            try:
                manager_activate(internal_id, stream_name, XiaomiQualityProfile(
                    previous_name, None, None, None, None, None, False,
                ))
                restored = verifier(stream_name, previous_name)
                rollback_ok = isinstance(restored, XiaomiQualityProfile) and restored.stable
                if rollback_ok and self.source_switch is not None:
                    resumed = self.source_switch(
                        f"rtsp://127.0.0.1:{self.go2rtc.rtsp_port}/{stream_name}",
                        self._public_device(device), restored,
                    )
                    rollback_ok = isinstance(resumed, Mapping) and resumed.get("status") in {"running", "connected", "ok"}
            except (OSError, RuntimeError, ValueError):
                pass
            return {
                "status": "failed",
                "success": False,
                "rollback_verified": rollback_ok,
                "message_zh_tw": (
                    "新畫質未取得影格；已恢復先前畫質並驗證影格。" if rollback_ok
                    else "新畫質未取得影格；已嘗試恢復先前畫質，尚未確認恢復，請查看診斷。"
                ),
            }
        self._profiles[internal_id] = [verified]
        self.camera_preferences.remember(internal_id, selected_name, stream_name, metadata={
            "width": verified.width, "height": verified.height, "fps": verified.fps,
            "video_codec": verified.video_codec, "audio_codec": verified.audio_codec})
        state = self._save(
            setup_stage="connected",
            selected_profile=selected_name,
            selected_stream_name=stream_name,
            latest_error_code=None,
            profile_error_message=None,
        )
        return {
            **state,
            "status": "ok",
            "success": True,
            "profile": selected_name,
            "video_verified": True,
            "source_metadata": {"width": verified.width, "height": verified.height,
                                "fps": verified.fps, "video_codec": verified.video_codec},
            "stream_name": stream_name,
            "message_zh_tw": f"已套用 {selected_name} 並取得有效影格：{verified.width}×{verified.height}；預覽尺寸可能另有縮放。",
        }

    @staticmethod
    def _safe_ptz_payload(payload: object) -> dict[str, Any]:
        """Keep only the documented, non-secret PTZ state fields."""

        if not isinstance(payload, Mapping):
            return {}
        allowed = {"connected", "can_left", "can_right", "can_up", "can_down"}
        safe: dict[str, Any] = {key: bool(payload[key]) for key in allowed if key in payload}
        nested_allowlists = {
            "position": {"angle", "elevation", "ret", "updated_at"},
            "limits": {"angle_min", "angle_max", "elevation_min", "elevation_max"},
            "capabilities": {"relative_move", "stop", "position", "absolute_move", "calibrate"},
        }
        for key, nested_allowed in nested_allowlists.items():
            value = payload.get(key)
            if isinstance(value, Mapping):
                safe[key] = {str(subkey): value[subkey] for subkey in nested_allowed if subkey in value}
        return safe

    def _ptz_stream(self, device: Mapping[str, Any]) -> str | None:
        """Return the one active local stream allowed for PTZ control."""

        selected = str(self._state.get("selected_stream_name") or "").strip()
        stream_name = str(device.get("stream_name") or "").strip()
        if not selected or not stream_name or selected != stream_name:
            return None
        if self.active_source_url is not None and self._active_local_stream_name() != selected:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", selected):
            return None
        return selected

    def _get_ptz_client(self) -> Any | None:
        client = self.ptz_client
        if client is None and self.source_manager is not None:
            client = getattr(self.source_manager, "client", None)
        return client

    def ptz_status(self, device_id: str, *, refresh: bool = False,
                   operation_id: str | None = None) -> dict[str, Any]:
        """Read PTZ state without moving a camera."""

        self._reconcile_active_selection()

        remote = self._resolve_device(device_id)
        if remote is not None and self._remote_active(*remote):
            internal_id, device = remote
            try:
                result = self._relay_control_client(internal_id, device).request("ptz_refresh" if refresh else "ptz_state")
            except RelayControlError:
                result = {"success": False}
            available = result.get("success") is True and result.get("ptz_available") is True
            state = self._safe_ptz_payload(result.get("state", {}))
            self._gate_absolute_move(device, state)
            position = state.get("position", {})
            verified = bool(refresh and result.get("position_readback_verified") is True and position.get("ret") == 0
                            and all(type(position.get(k)) is int and 0 <= position[k] <= 101 for k in ("angle", "elevation")))
            if refresh and operation_id:
                self._record_ptz_readback(operation_id, device_id, verified, position if verified else None)
            return {"status": "available" if available else "unavailable", "success": available,
                    "state": state, "position_readback_verified": verified,
                    "ptz_available": available, "message_zh_tw":
                    "遠端 Xiaomi PTZ 狀態已讀取。" if available else "遠端 Xiaomi PTZ 不可用；未執行鏡頭操作。"}
        if not self.experimental_ptz_enabled:
            return {
                "status": "disabled",
                "success": False,
                "ptz_available": False,
                "message_zh_tw": "目前使用官方 go2rtc；Xiaomi MISS PTZ 需要明確啟用隔離實驗版，未執行任何鏡頭操作。",
            }
        resolved = self._resolve_device(device_id)
        if resolved is None:
            return {"status": "not_found", "success": False, "ptz_available": False, "message_zh_tw": "找不到指定的小米攝影機。"}
        _, device = resolved
        stream_name = self._ptz_stream(device)
        if not stream_name:
            if refresh and operation_id:
                self._record_ptz_readback(operation_id, device_id, False, None)
            return {
                "status": "not_ready",
                "success": False,
                "ptz_available": False,
                "message_zh_tw": "尚未接入單一活動中的 Xiaomi 來源；請先完成畫質驗證與接入。",
            }
        client = self._get_ptz_client()
        read_state = getattr(client, "xiaomi_ptz_state", None)
        if not callable(read_state):
            if refresh and operation_id:
                self._record_ptz_readback(operation_id, device_id, False, None)
            return {
                "status": "unavailable",
                "success": False,
                "ptz_available": False,
                "message_zh_tw": "目前 go2rtc 執行檔沒有 Xiaomi MISS PTZ API；正式官方版本未被替換。",
            }
        try:
            payload = read_state(stream_name, refresh=bool(refresh))
        except (OSError, RuntimeError, ValueError) as exc:
            if refresh and operation_id:
                self._record_ptz_readback(operation_id, device_id, False, None)
            return {
                "status": "unavailable",
                "success": False,
                "ptz_available": True,
                "position_readback_verified": False,
                "message_zh_tw": (
                    "目前 Xiaomi 串流尚未活動；請先開始監控並確認即時影格，再讀取位置。這不是裝置不支援位置的證據。"
                    if "producer is not active" in str(exc)
                    else "位置讀回逾時；未收到馬達回應，不能判定裝置不支援。"
                    if "504" in str(exc) or "timeout" in str(exc).lower()
                    else "Xiaomi MISS PTZ 狀態讀取失敗；未執行鏡頭移動。"
                ),
            }
        safe_state = self._safe_ptz_payload(payload)
        self._gate_absolute_move(device, safe_state)
        position = safe_state.get("position", {})
        valid_position = (position.get("ret") == 0
                          and all(type(position.get(key)) is int and 0 <= position[key] <= 101
                                  for key in ("angle", "elevation")))
        if refresh and (not valid_position or safe_state.get("connected") is not True):
            if operation_id:
                self._record_ptz_readback(operation_id, device_id, False, None)
            return {"status": "position_unavailable", "success": False, "ptz_available": True,
                    "position_readback_verified": False, "state": safe_state,
                    "message_zh_tw": "攝影機未回傳有效位置；方向控制可能可用，但不可保存或恢復精確位置。"}
        if isinstance(payload, Mapping) and payload.get("connected") is False:
            if refresh and operation_id:
                self._record_ptz_readback(operation_id, device_id, False, None)
            return {
                "status": "failed",
                "success": False,
                "ptz_available": True,
                "stream_name": stream_name,
                "state": safe_state,
                "message_zh_tw": "Xiaomi MISS PTZ 狀態回報未連線；未執行鏡頭移動。",
            }
        if refresh and operation_id:
            self._record_ptz_readback(operation_id, device_id, valid_position, position if valid_position else None)
        return {
            "status": "available",
            "success": True,
            "ptz_available": True,
            "stream_name": stream_name,
            "state": safe_state,
            "position_readback_verified": bool(refresh and valid_position),
            "message_zh_tw": "已讀取 Xiaomi MISS PTZ 狀態。",
        }

    @staticmethod
    def _gate_absolute_move(device: Mapping[str, Any], state: dict[str, Any]) -> None:
        capabilities = state.setdefault("capabilities", {})
        model = str(device.get("model") or "").strip().lower()
        capabilities["absolute_move"] = bool(
            capabilities.get("absolute_move") is True and model in VERIFIED_ABSOLUTE_MOVE_MODELS
        )

    def _record_ptz_readback(self, operation_id: str, device_id: str,
                             verified: bool, position: Mapping[str, Any] | None) -> None:
        if new_operation_id(operation_id) != operation_id:
            return
        self.control_journal.record_readback(
            operation_id, device_id, verified=verified, position=position,
        )

    def ptz_action(
        self,
        device_id: str,
        *,
        action: str,
        direction: str | None = None,
        angle: int | None = None,
        elevation: int | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        operation_id = new_operation_id(operation_id)
        before_position: dict[str, int] | None = None
        try:
            cached = self.ptz_status(device_id, refresh=False)
            position = cached.get("state", {}).get("position", {})
            if (isinstance(position, Mapping) and position.get("ret") == 0
                    and type(position.get("angle")) is int and type(position.get("elevation")) is int
                    and 0 <= position["angle"] <= 101 and 0 <= position["elevation"] <= 101):
                before_position = {"angle": position["angle"], "elevation": position["elevation"]}
        except (OSError, RuntimeError, ValueError, TypeError):
            before_position = None
        target: dict[str, Any] = {"action": action}
        if direction in {"left", "right", "up", "down"}:
            target["direction"] = direction
        if type(angle) is int:
            target["angle"] = angle
        if type(elevation) is int:
            target["elevation"] = elevation
        try:
            result = self._ptz_action_impl(
                device_id, action=action, direction=direction, angle=angle, elevation=elevation,
            )
        except Exception as exc:  # noqa: BLE001 - audit without persisting transport details
            message = f"鏡頭控制發生錯誤（{type(exc).__name__}）；未確認實際移動。"
            self.control_journal.append(
                operation_id=operation_id, operation_type=f"ptz_{action}", camera_id=device_id,
                requested_target=target, command_sent=None, transport_result="error",
                readback_attempted=False, readback_result="not_attempted",
                final_status="failed", failure_reason=message, before_position=before_position,
            )
            return {"status": "failed", "success": False, "message_zh_tw": message,
                    "operation_id": operation_id,
                    "diagnostic_url": f"/diagnostics?operation_id={operation_id}#xiaomi-control-diagnostics"}
        status = str(result.get("status") or "failed")
        succeeded = result.get("success") is True
        if succeeded:
            final_status, command_sent, transport = "command_accepted_unverified", True, "response_received"
        elif status == "unsupported":
            final_status, command_sent, transport = "unsupported", False, "not_sent"
        elif status in {"disabled", "not_ready", "not_found", "unavailable"}:
            final_status, command_sent, transport = "failed", False, "not_attempted"
        else:
            final_status, command_sent, transport = "failed", None, "error_or_rejection"
            message = str(result.get("message_zh_tw") or "鏡頭控制失敗；未確認實際移動。")
            if "逾時" in message or "timeout" in message.lower():
                final_status = "timeout"
        self.control_journal.append(
            operation_id=operation_id, operation_type=f"ptz_{action}", camera_id=device_id,
            requested_target=target, command_sent=command_sent, transport_result=transport,
            readback_attempted=False, readback_result="pending" if succeeded else "not_attempted",
            final_status=final_status,
            failure_reason=None if succeeded else result.get("message_zh_tw"),
            before_position=before_position,
        )
        result = dict(result)
        result["operation_id"] = operation_id
        result["diagnostic_url"] = f"/diagnostics?operation_id={operation_id}#xiaomi-control-diagnostics"
        return result

    def _ptz_action_impl(
        self,
        device_id: str,
        *,
        action: str,
        direction: str | None = None,
        angle: int | None = None,
        elevation: int | None = None,
    ) -> dict[str, Any]:
        """Execute one user-requested, allow-listed PTZ command."""

        if action in {"move", "position"} and self.view_change is not None:
            self.view_change("movement")
        remote = self._resolve_device(device_id)
        if remote is not None and self._remote_active(*remote):
            internal_id, device = remote
            if action not in {"move", "stop", "position"}:
                return {"status": "unsupported", "success": False, "ptz_available": False,
                        "message_zh_tw": "遠端中繼只支援已驗證的方向移動與停止；未執行此操作。"}
            try:
                client = self._relay_control_client(internal_id, device)
                if action == "position":
                    state = self.ptz_status(device_id, refresh=True)
                    if not state.get("position_readback_verified") or state.get("state", {}).get("capabilities", {}).get("absolute_move") is not True:
                        return {"status": "unsupported", "success": False,
                                "message_zh_tw": "此攝影機尚無實機絕對定位成功證據；座標定位已停用，請使用方向鍵。"}
                    result = client.request("ptz_position", angle=angle, elevation=elevation)
                else:
                    result = client.request("ptz_move" if action == "move" else "ptz_stop", direction=direction)
            except RelayControlError:
                result = {"success": False}
            acknowledged = result.get("success") is True and result.get("status") == "acknowledged"
            return {"status": "acknowledged" if acknowledged else "failed", "success": acknowledged,
                    "ptz_available": bool(result.get("ptz_available")), "movement_verified": False,
                    "message_zh_tw": "遠端已回應方向指令；仍需由畫面確認鏡頭實際移動。" if acknowledged
                    else "遠端方向指令失敗或逾時；不能確認鏡頭已移動。"}
        if not self.experimental_ptz_enabled:
            return {
                "status": "disabled",
                "success": False,
                "ptz_available": False,
                "message_zh_tw": "目前使用官方 go2rtc；Xiaomi MISS PTZ 未啟用，未執行任何鏡頭操作。",
            }
        resolved = self._resolve_device(device_id)
        if resolved is None:
            return {"status": "not_found", "success": False, "ptz_available": False, "message_zh_tw": "找不到指定的小米攝影機。"}
        _, device = resolved
        stream_name = self._ptz_stream(device)
        if not stream_name:
            return {"status": "not_ready", "success": False, "ptz_available": False, "message_zh_tw": "尚未接入活動中的 Xiaomi 來源，未執行鏡頭操作。"}
        client = self._get_ptz_client()
        send_action = getattr(client, "xiaomi_ptz_action", None)
        if not callable(send_action):
            return {"status": "unavailable", "success": False, "ptz_available": False, "message_zh_tw": "目前 go2rtc 執行檔沒有 Xiaomi MISS PTZ API。"}
        if action == "position":
            state = self.ptz_status(device_id, refresh=True)
            if (not state.get("position_readback_verified")
                    or state.get("state", {}).get("capabilities", {}).get("absolute_move") is not True):
                return {"status": "unsupported", "success": False, "ptz_available": True,
                        "message_zh_tw": "此攝影機尚無實機絕對定位成功證據；座標定位已停用，請使用方向鍵。"}
        try:
            if action == "move":
                # Keep the motor pulse bounded even if the browser closes.
                try:
                    payload = send_action(stream_name, action, direction=direction, angle=angle, elevation=elevation)
                    time.sleep(0.3)
                finally:
                    stopped = send_action(stream_name, "stop")
                    if isinstance(stopped, Mapping) and stopped.get("connected") is False:
                        raise RuntimeError("PTZ 停止指令未確認；請檢查鏡頭。")
            else:
                payload = send_action(stream_name, action, direction=direction, angle=angle, elevation=elevation)
        except (OSError, RuntimeError, ValueError) as exc:
            message = str(exc)
            if "位置" in message or "方向" in message or "操作" in message:
                return {"status": "invalid", "success": False, "ptz_available": True, "message_zh_tw": message}
            return {"status": "failed", "success": False, "ptz_available": True, "message_zh_tw": "Xiaomi MISS PTZ 操作失敗；請確認實驗版 producer 仍在線。"}
        safe_state = self._safe_ptz_payload(payload)
        if isinstance(payload, Mapping) and payload.get("connected") is False:
            return {
                "status": "failed",
                "success": False,
                "ptz_available": True,
                "stream_name": stream_name,
                "state": safe_state,
                "message_zh_tw": "Xiaomi MISS PTZ 回報未連線；方向控制未成功。",
            }
        return {
            "status": "ok",
            "success": True,
            "ptz_available": True,
            "stream_name": stream_name,
            "state": safe_state,
            "message_zh_tw": "Xiaomi MISS PTZ 操作已送出。",
        }

    def _resolve_device(self, device_id: str) -> tuple[str, dict[str, Any]] | None:
        requested = str(device_id or "").strip()
        if not requested:
            return None
        internal_id = requested if requested in self._device_records else self._device_aliases.get(requested)
        if internal_id is None:
            # Compatibility with the masked identifier returned by older
            # Dashboard clients.
            for key, record in self._device_records.items():
                if _mask(record.get("device_id") or record.get("did")) == requested:
                    internal_id = key
                    break
        if internal_id is None:
            return None
        return internal_id, self._device_records[internal_id]

    def probe_device(self, device_id: str) -> dict[str, Any]:
        self._reconcile_active_selection()
        if not self._camera_control_lock.acquire(blocking=False):
            return {**self.status(), "action_status": "busy", "message_zh_tw": "攝影機正在套用其他操作，請稍候。"}
        try:
            return self._probe_device(device_id)
        finally:
            self._camera_control_lock.release()

    def _probe_device(self, device_id: str, *, profile_names: tuple[str, ...] = ("subtype=sd",)) -> dict[str, Any]:
        resolved = self._resolve_device(device_id)
        if resolved is None:
            return {**self.status(), "action_status": "not_found", "message_zh_tw": "找不到已載入的 Xiaomi 裝置；請先重新載入裝置。"}
        internal_id, device = resolved
        active_stream = self._ptz_stream(device)
        if active_stream:
            # Probe the existing local producer; never open a second Xiaomi
            # connection or replace the active profile with a temporary SD one.
            name = str(self._state.get("selected_profile") or "current")
            verifier = getattr(self.source_manager, "verify_active_profile", None)
            try:
                profile = verifier(active_stream, name) if callable(verifier) else None
                if not isinstance(profile, XiaomiQualityProfile) or not profile.stable:
                    raise RuntimeError("目前畫質資訊未取得。")
            except (OSError, RuntimeError, ValueError):
                state = self._save(setup_stage="connected", latest_error_code=None, profile_error_message=None)
                return {**state, "action_status": "metadata_unavailable", "message_zh_tw":
                        "目前攝影機已接入監控；本次畫質資訊讀取失敗或逾時，沒有更換來源或停止監控。"}
            self._profiles[internal_id] = [profile]
            state = self._save(setup_stage="connected", latest_error_code=None, profile_error_message=None)
            return {**state, "action_status": "ready", "message_zh_tw":
                    f"目前監控串流已驗證：{profile.width}×{profile.height}；沿用現有來源，未另開攝影機連線。"}
        source = str(device.get("source_url") or "")
        if not device.get("source_reference_present") or not source.startswith("xiaomi://"):
            message = "此裝置尚未確認可用的 Xiaomi 後端區域與 SD 影格；畫質測試已停止，沒有建立串流。請按「重新載入並確認區域」一次。"
            state = self._save(
                setup_stage="degraded",
                latest_error_code="XIAOMI_REGION_UNVERIFIED",
                selected_device_id_masked=_mask(internal_id),
                selected_model=device.get("model"),
                profile_error_message=message,
            )
            return {**state, "action_status": "blocked", "message_zh_tw": message}

        self._save(
            setup_stage="probing_profiles",
            selected_device_id_masked=_mask(internal_id),
            selected_model=device.get("model"),
            profile_error_message=None,
        )
        # The MVP quality check is deliberately one bounded SD probe.  Region
        # discovery already owns the sequential region checks; never turn a
        # button click into a profile sweep.
        names = profile_names
        probe = self.profile_probe
        if probe is None and self.source_manager is not None:
            probe = getattr(self.source_manager, "profile_probe", None)
        profiles = probe(device, names) if callable(probe) else [
            XiaomiQualityProfile("subtype=sd", None, None, None, None, None, False, error="目前沒有可用的畫質測試器。")
        ]
        self._profiles[internal_id] = profiles
        selected = choose_highest_stable_profile(profiles)
        if selected is None:
            reason = redact_diagnostic_text(profiles[0].error, limit=180) if profiles and profiles[0].error else "未取得影像串流"
            profile_message = (
                f"單一 SD 畫質測試未取得穩定影格（{reason}）；尚未接入監控。請勿連續重試。"
                "若攝影機位於另一住處，此按鈕只測本機直連；請改由來源設定接入攝影機所在地的 RTSP 中繼。"
            )
            state = self._save(
                setup_stage="degraded",
                latest_error_code="NO_STABLE_PROFILE",
                profile_error_message=profile_message,
            )
            return {**state, "action_status": "failed", "message_zh_tw": profile_message}
        state = self._save(
            setup_stage="stream_ready",
            selected_profile=selected.name,
            selected_stream_name=None,
            latest_error_code=None,
            profile_error_message=None,
        )
        summary = f"SD 畫質確認成功：{selected.width}×{selected.height}，{selected.video_codec or '影像編碼未提供'}，{selected.fps or 'FPS 未提供'} FPS。現在可接入監控。"
        return {**state, "action_status": "ready", "message_zh_tw": summary}

    def _camera_power_diagnostic(self, source: str) -> dict[str, Any]:
        """Read native standby independently of PTZ/video and saved privacy poses."""
        try:
            power = self.miot_power.request(source, timeout_seconds=20)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            power = {}
        if isinstance(power, Mapping) and power.get("success") is True and type(power.get("on")) is bool:
            return {"status": "verified", "on": power["on"],
                    "state": "on" if power["on"] else "standby"}
        return {"status": "unverified", "on": None, "state": "unknown"}

    def firewall_diagnostic(self, device_id: str) -> dict[str, Any]:
        """Read-only Windows preflight; never submit credentials or change rules."""

        resolved = self._resolve_device(device_id)
        if resolved is None:
            return {"status": "not_found", "message_zh_tw": "請先載入並選擇小米攝影機。"}
        internal_id, device = resolved
        camera_ip = urlsplit(str(device.get("source_url") or "")).hostname or ""
        if not camera_ip:
            return {"status": "not_checked", "message_zh_tw": "尚未取得這台攝影機的區網位址，未檢查防火牆。"}
        script = self.root / "scripts" / "Manage-XiaomiUdpFirewall.ps1"
        if os.name != "nt" or not script.is_file():
            return {"status": "not_checked", "message_zh_tw": "此安裝環境尚未提供 Windows 小米 UDP 診斷工具。"}
        stream_name = str(device.get("stream_name") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", stream_name):
            stream_name = ""
        command = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-Mode", "Diagnose", "-CameraIp", camera_ip,
            "-ApiPort", str(self.go2rtc.api_port), "-RtspPort", str(self.go2rtc.rtsp_port),
            "-SustainedSeconds", "0",
        ]
        if stream_name:
            command.extend(["-StreamName", stream_name])
        try:
            completed = subprocess.run(
                command, shell=False, capture_output=True, text=True, encoding="utf-8-sig",
                errors="replace", timeout=45, check=False,
            )
            result = json.loads(completed.stdout)
            if not isinstance(result, dict):
                raise TypeError("invalid diagnostic shape")
        except (OSError, subprocess.TimeoutExpired, TypeError, ValueError) as exc:
            return {"status": "not_checked", "error_type": type(exc).__name__, "message_zh_tw": "防火牆診斷未完成；影音監控不受影響。"}
        candidate = self._profiles.get(internal_id, [])
        reason = str(candidate[0].error or "").lower() if candidate else ""
        if "read udp" in reason or "udp" in reason and "timeout" in reason:
            result["previous_probe_category"] = "udp_timeout"
        elif "401" in reason or "unauthorized" in reason:
            result["previous_probe_category"] = "account_authentication_failed"
        elif "decoder" in reason or "ref with poc" in reason:
            result["previous_probe_category"] = "frame_decode_failed"
        else:
            result["previous_probe_category"] = "not_checked"
        result["message_zh_tw"] = (
            "偵測到 UDP 逾時，且未找到本程式的限縮允許規則；可能與 Windows 防火牆有關。修復需在攝影機旁的電腦明確授權 UAC。"
            if result.get("previous_probe_category") == "udp_timeout" and result.get("rule_status") == "absent"
            else "唯讀檢查完成；單張影格與持續取流是不同驗收項目，請依各自狀態判讀。"
        )
        # A reachable camera can complete CS2 setup while its native switch is
        # off. Never infer standby from a timeout, or turn it on as a diagnostic.
        result["camera_power"] = self._camera_power_diagnostic(str(device.get("source_url") or ""))
        if result["camera_power"]["on"] is False:
            result["status"] = "attention_required"
            result["next_action"] = "confirm_camera_power_on"
            result["message_zh_tw"] = (
                "已讀回攝影機原生開關為關閉，目前處於隱私待機，因此無法取得新畫面。"
                "若要恢復拍攝，請在米家 App 開啟攝影機，或使用已設定的隱私待機恢復操作，再確認新畫面。"
                "本次診斷沒有開啟攝影機。"
            )
        return result

    def activate(self, device_id: str) -> dict[str, Any]:
        self._reconcile_active_selection()
        if not self._camera_control_lock.acquire(blocking=False):
            return {"action_status": "busy", "success": False,
                    "message_zh_tw": "攝影機正在切換或接入；未建立另一個來源。"}
        try:
            return self._activate(device_id)
        finally:
            self._camera_control_lock.release()

    def _activate(self, device_id: str) -> dict[str, Any]:
        resolved = self._resolve_device(device_id)
        if resolved is None:
            state = self._save(setup_stage="device_selection_required", latest_error_code="DEVICE_NOT_FOUND")
            return {**state, "action_status": "not_found", "message_zh_tw": "找不到已載入的 Xiaomi 裝置；請先重新載入裝置。"}
        internal_id, device = resolved
        if self._ptz_stream(device) and self._active_frame_ready():
            return {**self.status(), "action_status": "connected", "message_zh_tw":
                    "這台攝影機已接入監控並持續收到影格；沿用目前來源。"}
        source = str(device.get("source_url") or "")
        if not device.get("source_reference_present") or not source.startswith("xiaomi://"):
            message = "尚未驗證此攝影機的後端區域與 SD 影格，不能接入。請先按「重新載入並確認區域」；未取得真實影格前不會啟動監控。"
            state = self._save(
                setup_stage="degraded",
                latest_error_code="XIAOMI_REGION_UNVERIFIED",
                selected_device_id_masked=_mask(internal_id),
                selected_model=device.get("model"),
                profile_error_message=message,
            )
            return {**state, "action_status": "blocked", "message_zh_tw": message}
        profiles = self._profiles.get(internal_id, [])
        selected = choose_highest_stable_profile(profiles)
        remembered = self._remembered_preference(internal_id, device)
        resume = remembered is not None and callable(getattr(self.source_manager, "verify_active_profile", None))
        if resume:
            # Historical settings are a selection, not current frame proof.
            selected = XiaomiQualityProfile(remembered["profile"], None, None, None, None, None, False)
        if selected is None:
            message = "尚未通過 SD 畫質測試；接入已停止。請先按「測試畫質」，確認取得穩定影格後再接入。"
            state = self._save(setup_stage="degraded", latest_error_code="PROFILE_NOT_PROBED", profile_error_message=message)
            return {**state, "action_status": "blocked", "message_zh_tw": message}
        index = next((idx for idx, item in enumerate(self._devices) if item.get("internal_id") == internal_id), 0) + 1
        stream_name = str((remembered or {}).get("stream_name") or device.get("stream_name") or f"xiaomi_camera_{index}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", stream_name):
            stream_name = f"xiaomi_camera_{index}"
        self._save(setup_stage="activating_source")
        if self.source_switch is None:
            message = "監控來源切換尚未接線；目前不會啟動攝影機監控。"
            state = self._save(setup_stage="degraded", latest_error_code="SOURCE_SWITCH_NOT_CONNECTED", profile_error_message=message)
            return {**state, "action_status": "blocked", "message_zh_tw": message}
        try:
            manager_activate = getattr(self.source_manager, "activate_stream", None) if self.source_manager is not None else None
            if callable(manager_activate):
                local_url = str(manager_activate(internal_id, stream_name, selected))
                if resume:
                    selected = self.source_manager.verify_active_profile(stream_name, selected.name)
                    if not isinstance(selected, XiaomiQualityProfile) or not selected.stable:
                        raise RuntimeError("已保存來源目前沒有有效影格。")
                    self._profiles[internal_id] = [selected]
            else:
                local_url = f"rtsp://127.0.0.1:{self.go2rtc.rtsp_port}/{stream_name}"
            public_device = self._public_device(device)
            result = self.source_switch(local_url, public_device, selected)
        except (OSError, RuntimeError, ValueError) as exc:
            message = f"小米來源切換失敗（{type(exc).__name__}）；目前未確認已接入，請查看診斷。"
            state = self._save(setup_stage="degraded", latest_error_code="SOURCE_SWITCH_FAILED", profile_error_message=message)
            return {**state, "action_status": "failed", "message_zh_tw": message}
        status = str(result.get("status", "failed")) if isinstance(result, Mapping) else "failed"
        if status in {"ok", "connected", "running", "starting"}:
            if selected.stable:
                self.camera_preferences.remember(internal_id, selected.name, stream_name, metadata={
                    "width": selected.width, "height": selected.height, "fps": selected.fps,
                    "video_codec": selected.video_codec, "audio_codec": selected.audio_codec})
            device["stream_name"] = stream_name
            self._devices = [
                {**item, "selected": item.get("internal_id") == internal_id, "stream_name": stream_name if item.get("internal_id") == internal_id else item.get("stream_name", "")}
                for item in self._devices
            ]
            starting = status == "starting"
            state = self._save(setup_stage="activating_source" if starting else "connected", latest_error_code=None, selected_device_id_masked=_mask(internal_id), selected_model=device.get("model"), selected_profile=selected.name, selected_stream_name=stream_name, profile_error_message=None)
            message = "小米來源已保存，監控正在啟動；請在總覽確認影格、AI 與環形快取。" if starting else "攝影機來源已接入；請前往「即時畫面」確認是否持續收到影格。"
            return {**state, "action_status": "starting" if starting else "connected", "message_zh_tw": message}
        message = "來源切換未成功；目前未確認已接入，請查看診斷後再試。"
        state = self._save(setup_stage="degraded", latest_error_code="SOURCE_SWITCH_FAILED", profile_error_message=message)
        return {**state, "action_status": "failed", "message_zh_tw": message}

    def disconnect(self) -> dict[str, Any]:
        source_error = False
        stream_name = self._state.get("selected_stream_name")
        if stream_name and self.source_manager is not None:
            try:
                remove_stream = getattr(self.source_manager, "remove_stream", None)
                if callable(remove_stream):
                    remove_stream(str(stream_name))
            except (OSError, RuntimeError, ValueError):
                source_error = True
        if self.source_switch is not None:
            try:
                self.source_switch("", {}, XiaomiQualityProfile("none", None, None, None, None, None, False))
            except (OSError, RuntimeError, ValueError):
                source_error = True
        return self._save(
            setup_stage="ready",
            selected_device_id_masked=None,
            selected_model=None,
            selected_profile=None,
            selected_stream_name=None,
            latest_error_code="SOURCE_DISCONNECT_FAILED" if source_error else None,
        )
