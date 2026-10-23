"""MeowKeeper+ 唯一正式 Host 啟動入口。"""

from __future__ import annotations

import argparse
import importlib
import ipaddress
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.app.source_config import (
    SourceConfig,
    SourceConfigService,
    load_go2rtc_rtsp_lan_access,
    load_runtime_settings,
    load_source_config,
    load_telegram_enabled,
    save_runtime_settings,
)
from meowkeeper.app.web.events import EventHub
from meowkeeper.app.web.main import create_app
from meowkeeper.app.web.network import (
    DEFAULT_DASHBOARD_HOST,
    DashboardNetworkService,
    listener_bind_matches,
    resolve_dashboard_config,
)
from meowkeeper.app.web.session import SessionAuthError, SessionManager
from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences
from meowkeeper.core.notifications.dispatcher import NotificationDispatcher
from meowkeeper.core.notifications.status import RuntimeStatusService
from meowkeeper.core.notifications.telegram import TelegramNotifier
from meowkeeper.core.notifications.telegram_commands import TelegramCommandRouter
from meowkeeper.core.notifications.telegram_feedback import TelegramFeedbackPoller
from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.go2rtc.lan_access import WindowsRtspFirewallManager
from meowkeeper.integrations.go2rtc.process import Go2RtcProcess
from meowkeeper.integrations.go2rtc.redaction import redact_diagnostic_text
from meowkeeper.integrations.tailscale.client import TailscaleClient
from meowkeeper.integrations.tailscale.status_cache import TailscaleStatusCache
from meowkeeper.integrations.xiaomi.device_discovery import Go2RtcXiaomiDiscovery
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService
from meowkeeper.process_guard import CrossProcessLock, port_owner
from meowkeeper.runtime import default_model_path
from meowkeeper.runtime_paths import (
    default_dashboard_config_path,
    default_data_root,
    default_install_root,
)
from meowkeeper.security.credentials import (
    CredentialStoreError,
    TelegramCredentials,
    TelegramCredentialStore,
)
from meowkeeper.storage.database import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="啟動喵喵守護者+ Host 與本機 Web Dashboard。")
    data_root = default_data_root(ROOT)
    parser.add_argument("--host", default=None, help="相容用 bind 覆寫；區網模式固定使用 0.0.0.0。")
    parser.add_argument("--port", type=int, default=None, help="Dashboard Port；未提供時讀取 dashboard 設定。")
    parser.add_argument(
        "--dashboard-config",
        default=str(default_dashboard_config_path(ROOT, data_root)),
        help="非秘密 Dashboard 設定檔；預設為目前安裝／可攜資料目錄內的 config.json。",
    )
    lan = parser.add_mutually_exclusive_group()
    lan.add_argument("--lan-access", dest="lan_access_enabled", action="store_true", help="允許同一區網存取 Dashboard。")
    lan.add_argument("--no-lan-access", dest="lan_access_enabled", action="store_false", help="只綁定 localhost。")
    parser.set_defaults(lan_access_enabled=None)
    browser = parser.add_mutually_exclusive_group()
    browser.add_argument("--open-browser", dest="open_browser", action="store_true", help="Health 成功後開啟瀏覽器。")
    browser.add_argument("--no-open-browser", dest="open_browser", action="store_false", help="不自動開啟瀏覽器。")
    parser.set_defaults(open_browser=True)
    parser.add_argument("--source-url", help="來源網址；RTSP 密碼請優先放在環境變數。")
    parser.add_argument("--source-url-env", default="MEOWKEEPER_MEDIA_SOURCE_URL")
    parser.add_argument("--disable-monitor", action="store_true", help="只啟動 Web，不自動開始監控。")
    parser.add_argument("--log-level", default="info", choices=("critical", "error", "warning", "info", "debug"))
    parser.add_argument("--dev", action="store_true", help="標示開發模式；仍遵守目前 Dashboard bind 設定。")
    parser.add_argument("--database-path", default=str(data_root / "meowkeeper.db"))
    parser.add_argument("--event-directory", default=str(data_root / "events"))
    parser.add_argument("--ring-directory", default=str(data_root / "cache" / "ring"))
    parser.add_argument("--preview-directory", default=str(data_root / "cache" / "preview"))
    parser.add_argument(
        "--go2rtc-api-port",
        type=int,
        default=_port_default("MEOWKEEPER_GO2RTC_API_PORT", 1984),
        help="localhost go2rtc API port；預設 1984。",
    )
    parser.add_argument(
        "--go2rtc-rtsp-port",
        type=int,
        default=_port_default("MEOWKEEPER_GO2RTC_RTSP_PORT", 8554),
        help="localhost go2rtc RTSP port；預設 8554。",
    )
    rtsp_lan = parser.add_mutually_exclusive_group()
    rtsp_lan.add_argument(
        "--go2rtc-rtsp-lan-access",
        dest="go2rtc_rtsp_lan_access",
        action="store_true",
        help="明確允許 Private／區網 TCP RTSP 8554；1984 API 仍只限 localhost。",
    )
    rtsp_lan.add_argument(
        "--no-go2rtc-rtsp-lan-access",
        dest="go2rtc_rtsp_lan_access",
        action="store_false",
        help="只綁定 localhost RTSP。",
    )
    parser.set_defaults(go2rtc_rtsp_lan_access=None)
    parser.add_argument(
        "--xiaomi-fallback-region",
        choices=("none", *Go2RtcXiaomiDiscovery.REGIONS),
        default="sg",
        help="自動探索沒有取得影格時，只把該區域清單內的同一裝置當作候選；預設 sg，可用 none 停用。",
    )
    parser.add_argument(
        "--enable-xiaomi-miss-ptz",
        action="store_true",
        default=os.getenv("MEOWKEEPER_XIAOMI_MISS_PTZ_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
        help="明確啟用已隔離的 Xiaomi MISS PTZ 實驗 API；官方 go2rtc v1.9.14 預設不啟用。",
    )
    parser.add_argument("--model", default=default_model_path())
    parser.add_argument("--analyzer", choices=("auto", "yolo", "v02", "noop"), default="auto",
                        help="auto 依 Dashboard 設定，預設找貓；v02 為實驗性追蹤／行為。")
    parser.add_argument("--ffmpeg-path")
    parser.add_argument(
        "--enable-telegram",
        action="store_true",
        help="明確啟用事件 Telegram 通知；憑證優先讀取 Credential Manager，亦可用環境變數。",
    )
    parser.add_argument("--telegram-token-env", default="MEOWKEEPER_TELEGRAM_BOT_TOKEN")
    parser.add_argument("--telegram-chat-ids-env", default="MEOWKEEPER_TELEGRAM_CHAT_IDS")
    parser.add_argument("--admin-user-env", default="MEOWKEEPER_WEB_ADMIN_USERNAME")
    parser.add_argument("--admin-password-env", default="MEOWKEEPER_WEB_ADMIN_PASSWORD")
    parser.add_argument("--admin-storage-path", default=str(data_root / "web_admin.json"), help="管理員雜湊保存位置；不得放入 Git。")
    parser.add_argument(
        "--lock-root",
        type=Path,
        default=Path(os.getenv("MEOWKEEPER_LOCK_ROOT") or default_install_root(ROOT)),
        help="單一實例診斷檔根目錄；正式安裝應指向 App 與 Data 的共同父目錄。",
    )
    return parser


def _source(args: argparse.Namespace) -> str | None:
    explicit = args.source_url or os.getenv(args.source_url_env)
    if explicit:
        return explicit
    config = load_source_config(args.dashboard_config)
    return config.url if config.configured else None


def _wait_for_source_frame(controller: Any, source_url: str, started_at: float, *, timeout: float = 20) -> bool:
    """A started thread is not a verified source; reject stale preview files."""
    from PIL import Image

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if controller.source_url != source_url:
            return False
        frame = controller.latest_jpeg_path
        if frame is not None:
            try:
                path = Path(frame)
                if path.stat().st_mtime >= started_at:
                    with Image.open(path) as image:
                        if image.format == "JPEG":
                            image.verify()
                            return True
            except (OSError, ValueError):
                pass
        if controller.get_status().get("status") in {"error", "stopped"}:
            return False
        time.sleep(0.1)
    return False


def _prepare_saved_go2rtc(source_url: str | None, bridge: Any, logger: Any) -> bool:
    """Restore only the managed loopback bridge; never reset its session/config."""
    try:
        parsed = urlsplit(source_url or "")
        managed = (parsed.scheme == "rtsp" and parsed.hostname == "127.0.0.1"
                   and parsed.port == bridge.rtsp_port and bool(parsed.path.strip("/"))
                   and parsed.username is None and parsed.password is None)
    except (ValueError, TypeError):
        return False
    if not managed:
        return False
    try:
        if bridge.status().healthy:
            return True
        if not bridge.config_path.is_file():
            logger("⚠️ 已保存 go2rtc 來源，但既有設定檔不存在；不建立空設定或切換 Mock，請在攝影機頁恢復設定。")
            return False
        result = bridge.start()
        logger("ℹ️ 已恢復前次 go2rtc 服務，將接回已保存來源。" if result.healthy
               else "⚠️ go2rtc 尚未就緒；保留前次來源，由影音管線重連，不切換 Mock。")
        return bool(result.healthy)
    except (OSError, RuntimeError, ValueError) as exc:
        logger(f"⚠️ 前次 go2rtc 服務恢復失敗：{type(exc).__name__}；保留來源設定。")
        return False


def _telegram_options(
    args: argparse.Namespace,
    credential_store: TelegramCredentialStore | None = None,
) -> tuple[bool, TelegramNotifier | None, tuple[str, ...]]:
    """Build the notification boundary without ever logging secret values.

    Telegram is deliberately opt-in.  Environment variables may be present for
    other diagnostics, but they do not enable outbound delivery by themselves.
    """

    config_enabled = load_telegram_enabled(args.dashboard_config)
    if not args.enable_telegram and not config_enabled:
        return False, None, ()
    token = os.getenv(args.telegram_token_env, "").strip()
    raw_chat_ids = os.getenv(args.telegram_chat_ids_env, "")
    stored = TelegramCredentials()
    if not token or not raw_chat_ids:
        try:
            if credential_store is not None:
                stored = credential_store.read_telegram()
        except (CredentialStoreError, OSError, RuntimeError):
            stored = TelegramCredentials()
        token = token or stored.bot_token
        selected_chat_ids = getattr(stored, "selected_chat_ids", None)
        if not raw_chat_ids and selected_chat_ids == () and getattr(stored, "recipients_json", ""):
            # Explicitly selecting nobody disables outbound delivery, not media.
            return False, None, ()
        stored_ids = stored.chat_ids if selected_chat_ids is None else ",".join(selected_chat_ids)
        raw_chat_ids = raw_chat_ids or stored_ids
    if not token:
        if not args.enable_telegram:
            # The Dashboard toggle is persisted separately from secrets.  A
            # missing optional Token must not stop the media pipeline.
            return False, None, ()
        raise ValueError(
            "已啟用 Telegram，但找不到 Bot Token。請在 Dashboard 安全保存或設定指定環境變數後重新啟動；"
            "Token 不要貼到日誌或命令列。"
        )
    chat_ids = tuple(dict.fromkeys(item.strip() for item in raw_chat_ids.split(",") if item.strip()))
    if not chat_ids:
        if not args.enable_telegram:
            return False, None, ()
        raise ValueError(
            "已啟用 Telegram，但找不到 Chat ID。請在 Dashboard 安全保存或設定指定環境變數後重新啟動；"
            "不要把 Chat ID 寫入公開設定。"
        )
    return True, TelegramNotifier(token), chat_ids


def _port_default(environment_name: str, fallback: int) -> int:
    """Read an optional localhost bridge port without accepting bad values."""

    value = os.getenv(environment_name, "").strip()
    if not value:
        return fallback
    try:
        port = int(value)
    except ValueError:
        return fallback
    return port if 1 <= port <= 65535 else fallback


def _wait_for_health(base_url: str, *, timeout_seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + "/healthz", timeout=1.0) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.2)
    return False


def _safe_tailscale_status(
    client: TailscaleClient,
    *,
    dashboard_network: DashboardNetworkService | None,
    dashboard_port: int,
) -> dict[str, Any]:
    """Return a read-only, secret-free Tailscale/Dashboard summary.

    This helper deliberately never runs ``tailscale up``, ``serve`` or
    ``funnel``.  A Tailnet URL is advertised only when the official client is
    logged in and the Dashboard is actually reachable on a non-loopback bind.
    """

    try:
        raw_status = client.status().to_dict()
    except Exception as exc:  # noqa: BLE001 - diagnostics must not stop Host
        raw_status = {
            "installed": bool(client.executable),
            "logged_in": False,
            "status": "not_checked",
            "message_zh_tw": f"Tailscale 狀態尚未確認：{type(exc).__name__}。",
        }
    try:
        raw_serve = client.serve_status()
    except Exception:  # noqa: BLE001 - Serve is optional and read-only
        raw_serve = {}

    tailscale_ip = raw_status.get("tailscale_ip")
    safe_ip: str | None = None
    try:
        parsed_ip = ipaddress.ip_address(str(tailscale_ip))
        if parsed_ip.version == 4 and parsed_ip in ipaddress.ip_network("100.64.0.0/10"):
            safe_ip = str(parsed_ip)
    except ValueError:
        safe_ip = None

    def safe_url(value: Any) -> str | None:
        candidate = str(value or "").strip()
        if len(candidate) > 256 or not candidate.startswith(("https://", "http://")):
            return None
        if any(character in candidate for character in ("\r", "\n", "@")):
            return None
        parsed = urlsplit(candidate)
        if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
        return candidate

    serve_url = safe_url(raw_status.get("serve_url"))
    if isinstance(raw_serve, Mapping):
        for key in ("url", "URL", "serve_url", "ServeURL"):
            serve_url = serve_url or safe_url(raw_serve.get(key))
    serve_enabled = bool(raw_status.get("serve_enabled")) or bool(
        isinstance(raw_serve, Mapping)
        and (
            raw_serve.get("enabled") is True
            or raw_serve.get("Enabled") is True
            or raw_serve.get("ServeConfig")
            or raw_serve.get("TCP")
        )
    )

    lan_enabled = bool(getattr(getattr(dashboard_network, "config", None), "lan_access_enabled", False))
    remote_url = serve_url if serve_enabled else None
    if remote_url is None and safe_ip and lan_enabled:
        remote_url = f"http://{safe_ip}:{int(dashboard_port)}"
    remote_status = "available" if remote_url else ("not_configured" if raw_status.get("logged_in") else "unavailable")
    device_list_available = bool(raw_status.get("device_list_available", False))
    safe_devices = [
        {
            "device_ref": str(item.get("device_ref") or ""),
            "hostname": item.get("hostname"),
            "dns_name": item.get("dns_name"),
            "os": item.get("os"),
            "online": item.get("online"),
            "active": item.get("active"),
            "last_seen": item.get("last_seen"),
            "tailnet_ips": list(item.get("tailnet_ips") or []),
            "is_self": bool(item.get("is_self", False)),
        }
        for item in raw_status.get("devices", [])
        if isinstance(item, Mapping) and item.get("device_ref")
    ] if isinstance(raw_status.get("devices"), (list, tuple)) and device_list_available else []
    return {
        "installed": bool(raw_status.get("installed", False)),
        "version": raw_status.get("version"),
        "logged_in": bool(raw_status.get("logged_in", False)),
        "service_running": raw_status.get("service_running"),
        "tailscale_ip": safe_ip,
        "device_name": raw_status.get("device_name"),
        "magic_dns_name": raw_status.get("magic_dns_name"),
        "serve_enabled": serve_enabled,
        "serve_url": serve_url,
        "funnel_enabled": False,
        "status": str(raw_status.get("status") or "not_checked"),
        "message_zh_tw": raw_status.get("message_zh_tw"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "remote_dashboard": {"status": remote_status, "url": remote_url},
        "device_list_available": device_list_available,
        # This API is session-protected. Addresses are used only to match the
        # actual socket peer; the browser response omits them.
        "devices": safe_devices,
    }


def _build_startup_notice(
    *,
    status_service: RuntimeStatusService | None = None,
    dashboard_snapshot: Mapping[str, Any] | None = None,
    tailscale: Mapping[str, Any] | None = None,
    source_type: str = "unknown",
    source_configured: bool = False,
) -> str:
    """Build a short startup message without source URLs or credentials."""

    if status_service is not None:
        return status_service.startup_notice()

    dashboard_snapshot = dashboard_snapshot or {}
    tailscale = tailscale or {}
    urls = [str(item) for item in dashboard_snapshot.get("urls", []) if str(item).startswith("http://")]
    local_url = str(dashboard_snapshot.get("local_url") or "http://127.0.0.1:8000")
    remote = tailscale.get("remote_dashboard") if isinstance(tailscale.get("remote_dashboard"), Mapping) else {}
    remote_url = str(remote.get("url") or "") if isinstance(remote, Mapping) else ""
    lines = ["喵喵守護者+ 已啟動", ""]
    lines.append(f"LAN Dashboard：{'、'.join(urls) if urls else '未啟用區網存取'}")
    lines.append(f"本機 Dashboard：{local_url}")
    lines.append(f"Tailscale Dashboard：{remote_url if remote_url.startswith(('http://', 'https://')) else 'Unavailable'}")
    lines.append(f"來源：{source_type if source_configured else 'Disabled'}")
    lines.append("AI：YOLO 設定已載入（實際推論以總覽狀態為準）")
    lines.append("Telegram：Enabled（啟動摘要已排入通知佇列）")
    return "\n".join(lines)


def _resolve_dashboard(args: argparse.Namespace) -> DashboardNetworkService:
    config_path = Path(args.dashboard_config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = resolve_dashboard_config(
        config_path,
        host=args.host,
        port=args.port,
        lan_access_enabled=args.lan_access_enabled,
    )
    args.host = config.bind_host
    args.port = config.port
    args.lan_access_enabled = config.lan_access_enabled
    args.dashboard_config = str(config_path.resolve())
    return DashboardNetworkService(config, config_path=args.dashboard_config)


def _build_runtime(
    args: argparse.Namespace,
    *,
    host_restart: Any | None = None,
) -> tuple[Any, RepositoryCoreController, SQLiteStore]:
    store = SQLiteStore(args.database_path)
    hub = EventHub()
    recent_logs: deque[str] = deque(maxlen=200)
    log_lock = threading.Lock()

    def record_log(message: str) -> None:
        safe = redact_diagnostic_text(message, limit=500)
        with log_lock:
            recent_logs.append(f"[{time.strftime('%H:%M:%S')}] {safe}")

    def log_snapshot() -> list[str]:
        with log_lock:
            return list(recent_logs)

    source_config = SourceConfigService(args.dashboard_config)
    source_settings = load_source_config(args.dashboard_config)
    rtsp_lan_enabled = load_go2rtc_rtsp_lan_access(args.dashboard_config)
    if args.go2rtc_rtsp_lan_access is not None:
        rtsp_lan_enabled = bool(args.go2rtc_rtsp_lan_access)
    runtime_settings = load_runtime_settings(args.dashboard_config)
    from meowkeeper.app.vision_settings import VisionSettingsStore
    vision_settings = VisionSettingsStore(Path(args.event_directory).resolve().parent, store=store)
    vision_provider = None
    vision_grace = 5
    try:
        vision_provider = vision_settings.build_provider()
        vision_grace = vision_settings.read()["grace_seconds"]
    except (OSError, ValueError, RuntimeError):
        record_log("可選視覺服務設定無法載入，略過進階分析；本機找貓與影片流程繼續。")
    effective_analyzer = runtime_settings["analyzer_mode"] if args.analyzer == "auto" else args.analyzer
    try:
        credential_store: TelegramCredentialStore | None = TelegramCredentialStore()
    except (CredentialStoreError, OSError, RuntimeError):
        credential_store = None
    telegram_enabled, telegram_notifier, telegram_chat_ids = _telegram_options(args, credential_store)
    recipient_roles: dict[str, str] = {}
    if telegram_enabled and credential_store is not None:
        try:
            saved_telegram = credential_store.read_telegram()
            selected_ids = set(telegram_chat_ids)
            recipient_roles = {
                recipient.chat_id: recipient.role
                for recipient in saved_telegram.effective_recipients
                if recipient.enabled and recipient.chat_id in selected_ids
                and recipient.chat_id.isdecimal() and int(recipient.chat_id) > 0
            }
        except (CredentialStoreError, OSError, RuntimeError):
            recipient_roles = {}
    for chat_id in telegram_chat_ids:
        if chat_id.isdecimal() and int(chat_id) > 0:
            recipient_roles.setdefault(chat_id, "viewer")
    telegram_dispatcher = (
        NotificationDispatcher(max_pending=64, logger=record_log)
        if telegram_enabled and telegram_notifier is not None and telegram_chat_ids else None
    )
    cat_notification_preferences = CatNotificationPreferences(
        Path(args.event_directory).resolve().parent / "state" / "telegram-cat-notifications.json"
    )
    dashboard_network = getattr(args, "dashboard_network", None)
    tailscale_client = TailscaleClient()
    tailscale_cache = TailscaleStatusCache(
        lambda: _safe_tailscale_status(
                tailscale_client,
                dashboard_network=dashboard_network,
                dashboard_port=int(args.port),
        ),
        ttl_seconds=10.0,
    )

    def tailscale_status() -> dict[str, Any]:
        return tailscale_cache.get()

    controller = RepositoryCoreController(
        store=store,
        vision_provider=vision_provider,
        semantic_wait_seconds=vision_grace,
        source_url=_source(args),
        model_path=args.model,
        ffmpeg_path=args.ffmpeg_path,
        ring_directory=args.ring_directory,
        event_directory=args.event_directory,
        preview_directory=args.preview_directory,
        retention_seconds=runtime_settings["retention_seconds"],
        maximum_size_mb=int(runtime_settings["ring_maximum_size_mb"]),
        analysis_fps=int(runtime_settings["analysis_fps"]),
        analysis_width=int(runtime_settings["analysis_width"]),
        inference_device=str(runtime_settings["inference_device"]),
        activity_fps=int(runtime_settings["activity_fps"]),
        identity_frame_width=int(runtime_settings["identity_frame_width"]),
        preview_frame_width=int(runtime_settings["preview_frame_width"]),
        confidence_threshold=float(runtime_settings["confidence_threshold"]),
        identity_accepted_threshold=float(runtime_settings.get("identity_accepted_threshold", 0.62)),
        identity_minimum_margin=float(runtime_settings.get("identity_minimum_margin", 0.05)),
        event_pre_seconds=float(runtime_settings["event_pre_seconds"]),
        event_post_seconds=float(runtime_settings["event_post_seconds"]),
        cooldown_seconds=float(runtime_settings["cooldown_seconds"]),
        analyzer_mode=effective_analyzer,
        telegram_enabled=telegram_enabled,
        telegram_notifier=telegram_notifier,
        telegram_chat_ids=telegram_chat_ids,
        telegram_dispatcher=telegram_dispatcher,
        cat_notification_preferences=cat_notification_preferences,
        tailscale_status_callback=tailscale_status,
        logger=record_log,
        event_sink=lambda payload: hub.publish("event", payload),
    )

    def save_settings(values: dict[str, Any]) -> dict[str, Any]:
        if ("analyzer_mode" in values and args.analyzer != "auto"
                and values["analyzer_mode"] != args.analyzer):
            raise ValueError("分析器已由啟動參數固定，請使用 --analyzer auto 才能從頁面修改。")
        runtime_values = {name: value for name, value in values.items() if name in runtime_settings}
        if runtime_values:
            save_runtime_settings(args.dashboard_config, runtime_values)
            return controller.apply_settings(runtime_values)
        # Network settings are persisted by DashboardNetworkService; the core
        # callback must not reject that separate, already validated boundary.
        return {"status": "accepted", "restart_required": False}

    # CoreStateFacade invokes this callback after API validation.  Secrets are
    # intentionally not part of the callback or persisted runtime section.
    controller.set_settings_callback(save_settings)
    def switch_xiaomi_source(local_url: str, _device: Any, _profile: Any) -> dict[str, Any]:
        previous = controller.source_url
        previous_config = source_config.config()
        was_running = controller.is_running
        try:
            if was_running:
                controller.stop_monitor(confirm=True)
            controller.set_source(local_url or None)
            if local_url:
                started_at = time.time()
                result = controller.start_monitor()
                if result.get("status") not in {"starting", "running"}:
                    raise RuntimeError("監控核心未接受小米來源")
                if not _wait_for_source_frame(controller, local_url, started_at):
                    raise RuntimeError("小米來源未取得本輪有效影格")
                source_config.save(SourceConfig(source_type="rtsp", url=local_url))
                app.state.core.set_source_summary(configured=True, source_type="rtsp")
                return {**result, "status": "running", "video_verified": True,
                        "message_zh_tw": "小米來源已取得本輪有效影格並保存；監控已開始。"}
            source_config.save(SourceConfig())
            app.state.core.set_source_summary(configured=False, source_type="mock")
            return {"status": "ok", "message_zh_tw": "已解除小米來源。"}
        except Exception as exc:  # noqa: BLE001 - preserve previous source on failure
            try:
                if controller.is_running:
                    controller.stop_monitor(confirm=True)
                controller.set_source(previous)
                if source_config.config() != previous_config:
                    source_config.save(previous_config)
                if was_running and previous:
                    controller.start_monitor()
            except (OSError, RuntimeError, ValueError) as rollback_error:
                _ = rollback_error
            return {"status": "failed", "message_zh_tw": f"來源切換失敗：{type(exc).__name__}"}
    # The official go2rtc process/session is owned outside the Dashboard
    # request path.  Discovery reads its localhost API; it never submits
    # Xiaomi credentials or starts a second camera connection.
    go2rtc = Go2RtcProcess(
        args.__dict__.get("root", ROOT),
        api_port=args.go2rtc_api_port,
        rtsp_port=args.go2rtc_rtsp_port,
    )
    if go2rtc.experimental_ptz:
        args.enable_xiaomi_miss_ptz = True
        record_log("ℹ️ 已選擇通過 026c02 位置讀回驗證的 Xiaomi MISS PTZ 實驗後端；官方版本仍保留。")
    rtsp_firewall_ready = False
    if rtsp_lan_enabled:
        # Fail closed: a wildcard RTSP bind is never attempted until the
        # narrow Private/localsubnet rule is confirmed for this executable.
        firewall_result = WindowsRtspFirewallManager().enable(
            port=args.go2rtc_rtsp_port,
            executable=go2rtc.executable,
        )
        rtsp_firewall_ready = bool(firewall_result.get("enabled"))
        if not rtsp_firewall_ready:
            record_log("⚠️ RTSP 區網已設定但防火牆規則未確認；本次維持 localhost，未暴露 8554。")
        else:
            go2rtc.rtsp_host = "0.0.0.0"
            go2rtc.rtsp_lan_firewall_ready = True
    if not args.disable_monitor:
        _prepare_saved_go2rtc(_source(args), go2rtc, record_log)
    xiaomi_bridge = Go2RtcXiaomiDiscovery(
        Go2RtcClient(base_url=go2rtc.api_url),
        region="auto",
        fallback_region=None if args.xiaomi_fallback_region == "none" else args.xiaomi_fallback_region,
        rtsp_port=go2rtc.rtsp_port,
        region_cache_path=(
            Path(os.environ["LOCALAPPDATA"]) / "MeowKeeperPlus" / "state" / "xiaomi-regions.json"
            if os.getenv("LOCALAPPDATA")
            else ROOT / "data" / "state" / "xiaomi-regions.json"
        ),
        region_cache_fallback_path=ROOT / "data" / "state" / "xiaomi-regions.json",
    )
    def active_source_frame_ready() -> bool:
        path = controller.latest_jpeg_path
        try:
            return bool(controller.is_running and path and time.time() - path.stat().st_mtime < 15)
        except OSError:
            return False

    xiaomi = XiaomiSetupService(
        args.__dict__.get("root", ROOT),
        go2rtc=go2rtc,
        discovery=xiaomi_bridge.discover,
        profile_probe=xiaomi_bridge.profile_probe,
        source_manager=xiaomi_bridge,
        source_switch=switch_xiaomi_source,
        active_source_url=lambda: controller.source_url,
        active_source_frame_ready=active_source_frame_ready,
        experimental_ptz_enabled=args.enable_xiaomi_miss_ptz,
        ptz_client=xiaomi_bridge.client,
        view_change=controller.invalidate_scene_view,
    )
    xiaomi.source_pause = lambda: controller.stop_monitor(confirm=True)
    from meowkeeper.integrations.xiaomi.relay_firewall import RelayFirewall
    xiaomi.relay_provider.network = tailscale_status
    xiaomi.relay_provider.firewall = RelayFirewall(ROOT, tailscale_status).verified
    # Explicit snapshots may use the native-resolution frame endpoint from
    # the already active local go2rtc producer. Browser preview remains the
    # bounded ingest JPEG, so no second camera/FFmpeg connection is created.
    def native_snapshot() -> bytes | None:
        try:
            source = urlsplit(str(controller.source_url or ""))
            if (
                source.scheme == "rtsp"
                and source.hostname == "127.0.0.1"
                and source.port == go2rtc.rtsp_port
                and not source.username
                and not source.password
                and not source.query
                and not source.fragment
            ):
                stream_name = source.path.removeprefix("/")
                if stream_name and all(char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in stream_name):
                    return xiaomi_bridge.client.get_frame_jpeg(stream_name, timeout_seconds=12.0)
        except (OSError, RuntimeError, ValueError):
            pass
        return xiaomi.native_snapshot_jpeg()

    controller.set_native_snapshot_provider(native_snapshot)
    def dashboard_health() -> bool:
        try:
            with urllib.request.urlopen(
                f"http://{DEFAULT_DASHBOARD_HOST}:{int(args.port)}/healthz", timeout=0.8,
            ) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    status_service = RuntimeStatusService(
        controller=controller,
        store=store,
        dashboard_network=dashboard_network,
        go2rtc_status=xiaomi.go2rtc.status,
        tailscale_status=tailscale_status,
        dashboard_health=dashboard_health,
        telegram_enabled=telegram_enabled,
        privacy_adapter_configured=bool(args.enable_xiaomi_miss_ptz),
        privacy_status=xiaomi.privacy_adapter.read_privacy_state if args.enable_xiaomi_miss_ptz else None,
        telegram_queue_pending=lambda: telegram_dispatcher.pending_count if telegram_dispatcher else 0,
        source_type=source_settings.source_type if source_settings.configured else ("external" if _source(args) else "mock"),
        source_configured=bool(_source(args)),
        logger=record_log,
    )
    # Reconcile an already authenticated external session once at Host
    # startup.  This is read-only: it only reads the existing go2rtc account
    # and device APIs, and repairs stale Dashboard state without opening a
    # login flow, probing profiles, or rewriting go2rtc configuration.  A
    # previous profile/P2P failure must not hide the account's real device
    # list after a process restart.
    try:
        if xiaomi.go2rtc.status().healthy:
            xiaomi.refresh()
    except (OSError, RuntimeError, ValueError):
        pass
    if xiaomi.relay_provider.path.is_file():
        threading.Thread(target=xiaomi.relay_provider.restore, name="relay-restore", daemon=True).start()
    sessions = SessionManager(storage_path=Path(args.admin_storage_path).resolve())
    admin_user = os.getenv(args.admin_user_env, "").strip()
    admin_password = os.getenv(args.admin_password_env, "")
    if admin_user or admin_password:
        if not admin_user or not admin_password:
            raise SessionAuthError("管理者帳號與密碼環境變數必須同時提供。")
        sessions.configure_admin(admin_user, admin_password)
    settings = {
        "source_configured": bool(_source(args)),
        "source_type": source_settings.source_type,
        "analyzer": effective_analyzer,
        "analyzer_override": args.analyzer != "auto",
        "development_mode": bool(args.dev),
        "lan_access_enabled": bool(args.lan_access_enabled),
        "dashboard_host": args.host,
        "dashboard_port": args.port,
        "telegram_enabled": telegram_enabled,
        **runtime_settings,
    }
    app = create_app(
        core_controller=controller,
        settings=settings,
        event_hub=hub,
        session_manager=sessions,
        integrations=xiaomi,
        dashboard_network=dashboard_network,
        source_config=source_config,
        host_restart=host_restart,
        telegram_credentials=credential_store,
        status_service=status_service,
        vision_settings=vision_settings,
    )
    app.state.development_mode = bool(args.dev)
    recorder = getattr(app.state, "resource_recorder", None)
    status_service.resource_history = recorder.history if recorder is not None else None
    app.state.log_snapshot = log_snapshot
    app.state.tailscale_status_callback = tailscale_status
    app.state.startup_notice_context = {
        "enabled": bool(runtime_settings.get("telegram_startup_notice")) and telegram_enabled,
        "telegram_enabled": telegram_enabled,
        "notifier": telegram_notifier,
        "dispatcher": telegram_dispatcher,
        "chat_ids": telegram_chat_ids,
        "recipient_roles": recipient_roles,
        "cat_notification_preferences": cat_notification_preferences,
        "logger": record_log,
        "source_type": source_settings.source_type if source_settings.configured else ("external" if _source(args) else "mock"),
        "source_configured": bool(_source(args)),
        "tailscale_status": tailscale_status,
        "go2rtc_status": xiaomi.go2rtc.status,
        "status_service": status_service,
        "privacy_service": xiaomi.privacy_service,
        "host_restart": host_restart,
        "live_seconds": float(runtime_settings["event_pre_seconds"]) + float(runtime_settings["event_post_seconds"]),
        "ring_retention_seconds": float(runtime_settings["retention_seconds"]),
    }
    if args.dev:
        app.state.dev_notice = "開發模式：仍遵守 Dashboard bind 設定；未設定的功能不會被視為已完成。"
    return app, controller, store


def configure_cpu_wait_policy() -> None:
    """Avoid idle OpenMP spin between sampled frames; preserve explicit overrides.

    This must run before lazy PyTorch/OpenCV initialization. It changes only
    this Host process environment, not Windows or the user's saved settings.
    """
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    os.environ.setdefault("KMP_BLOCKTIME", "0")


def main(argv: list[str] | None = None) -> int:
    configure_cpu_wait_policy()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        args.dashboard_network = _resolve_dashboard(args)
    except (OSError, ValueError) as exc:
        print(f"❌ Dashboard 網路設定不安全或無法讀取：{exc}", file=sys.stderr)
        return 2
    if not 1 <= args.port <= 65535:
        print("❌ Port 必須介於 1 到 65535。", file=sys.stderr)
        return 2
    if not 1 <= args.go2rtc_api_port <= 65535 or not 1 <= args.go2rtc_rtsp_port <= 65535:
        print("go2rtc API/RTSP port 必須介於 1 到 65535。", file=sys.stderr)
        return 2
    health_url = f"http://{DEFAULT_DASHBOARD_HOST}:{args.port}"
    dashboard_snapshot = args.dashboard_network.snapshot()
    display_urls = dashboard_snapshot.get("urls") or [dashboard_snapshot.get("local_url")]
    base_url = str(display_urls[0])
    # Startup ordering is intentional: reuse a healthy Host first, then take
    # the cross-process PID/mutex lock, then reject a port occupied by an
    # unrelated process.  This closes the race where two launchers both see a
    # closed health endpoint and spawn duplicate uvicorn servers.
    if _wait_for_health(health_url, timeout_seconds=1.0):
        bind_matches = listener_bind_matches(args.port, args.host)
        if bind_matches is not False:
            print(f"ℹ️ MeowKeeper+ Host 已在 {health_url} 運作，沿用既有程序。")
            return 0
        print("❌ 已有健康 Host，但目前 bind 與要求不同；請由啟動器安全重啟，未啟動第二個 Host。", file=sys.stderr)
        return 3
    instance_lock = CrossProcessLock("MeowKeeperPlusHost", root=args.lock_root)
    if not instance_lock.acquire():
        if _wait_for_health(health_url, timeout_seconds=15.0):
            print(f"ℹ️ MeowKeeper+ Host 正由另一個啟動程序準備，沿用 {health_url}。")
            return 0
        print("❌ MeowKeeper+ Host 單一實例鎖已被占用，但 /healthz 未回應；未啟動第二個 Host。", file=sys.stderr)
        return 3
    owners = port_owner(args.port, args.host)
    if owners:
        instance_lock.release()
        owner_pids = ", ".join(str(item.pid) for item in owners)
        print(f"❌ Host port {args.port} 已由 PID {owner_pids} 占用；未啟動第二個 Host。", file=sys.stderr)
        return 3
    restart_requested = False
    restart_monitor_requested = True
    server: Any | None = None
    telegram_dispatcher: NotificationDispatcher | None = None
    telegram_feedback_poller: TelegramFeedbackPoller | None = None
    telegram_command_router: TelegramCommandRouter | None = None

    def request_host_restart() -> dict[str, Any]:
        """Ask this process to shut down cleanly and re-exec the same venv.

        Re-exec reloads the current repository code and config using the same
        absolute interpreter. Windows may assign a new PID during ``execv``;
        the cross-process lock and port check still prevent an overlapping Host.
        A short timer lets the authenticated HTTP response reach the browser
        before Uvicorn closes its listener.
        """

        nonlocal restart_requested, restart_monitor_requested
        restart_requested = True
        restart_monitor_requested = controller.get_status().get("status") in {"running", "starting"}

        def stop_server() -> None:
            if server is not None:
                server.should_exit = True

        timer = threading.Timer(0.35, stop_server)
        timer.daemon = True
        timer.start()
        return {
            "status": "restarting",
            "message_zh_tw": "Host 即將重新啟動並重新載入最新程式與設定。",
        }

    try:
        import uvicorn

        try:
            importlib.import_module("websockets.asyncio.client")
            importlib.import_module("uvicorn.protocols.websockets.websockets_impl")
        except ImportError as exc:
            raise RuntimeError("內附 WebSocket 播放元件不完整；請使用已驗證的完整發布包。") from exc

        app, controller, store = _build_runtime(args, host_restart=request_host_restart)
        def request_host_shutdown() -> dict[str, Any]:
            nonlocal restart_requested
            restart_requested = False
            if getattr(app.state, "closing_requested", False):
                return {"status": "closing", "message_zh_tw": "完整關閉已排程。"}
            # Reuse the Tray's ownership-verified helper, without recursively
            # calling this endpoint or creating another camera consumer.
            subprocess.Popen(
                [sys.executable, str(ROOT / "tools/close_application.py"),
                 "--root", str(ROOT), "--already-closing"],
                cwd=str(ROOT), shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            app.state.closing_requested = True
            def stop_server() -> None:
                if server is not None:
                    server.should_exit = True
            timer = threading.Timer(0.35, stop_server)
            timer.daemon = True
            timer.start()
            return {"status": "closing", "message_zh_tw": "正在完整關閉此程式、錄影、Dashboard 與 Tray；請稍候。"}
        app.state.host_shutdown = request_host_shutdown
    except (ImportError, RuntimeError, ValueError, SessionAuthError) as exc:
        instance_lock.release()
        print(f"❌ Host 尚未準備完成：{exc}", file=sys.stderr)
        return 2

    if not args.disable_monitor and _source(args):
        result = controller.start_monitor()
        print(f"ℹ️ {result.get('message_zh_tw', '監控初始化中。')}")
    from meowkeeper.app.console_logging import dashboard_log_config

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level=args.log_level, reload=False,
                            ws="websockets",
                            log_config=dashboard_log_config(uvicorn.config.LOGGING_CONFIG))
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="meowkeeper-uvicorn", daemon=True)
    thread.start()
    try:
        healthy = _wait_for_health(health_url)
        server_started = bool(getattr(server, "started", False))
        if not healthy or not thread.is_alive() or not server_started:
            print("❌ Web Server 未能綁定指定 port 或在逾時內回應 /healthz。", file=sys.stderr)
            server.should_exit = True
            thread.join(timeout=5.0)
            return 2
        print(f"✅ 喵喵守護者+ Dashboard 已啟動：{base_url}/")
        startup_context = getattr(app.state, "startup_notice_context", {})
        notifier = startup_context.get("notifier") if isinstance(startup_context, Mapping) else None
        chat_ids = tuple(startup_context.get("chat_ids", ())) if isinstance(startup_context, Mapping) else ()
        telegram_dispatcher = startup_context.get("dispatcher") if isinstance(startup_context, Mapping) else None
        log_callback = startup_context.get("logger") if isinstance(startup_context, Mapping) else None
        feedback_chat_ids = tuple(
            chat_id for chat_id in chat_ids
            if chat_id.isdecimal() and int(chat_id) > 0
        )
        if startup_context.get("telegram_enabled") and notifier is not None and feedback_chat_ids:
            if telegram_dispatcher is None:
                telegram_dispatcher = NotificationDispatcher(max_pending=64, logger=log_callback)
            telegram_command_router = TelegramCommandRouter(
                notifier=notifier,
                dispatcher=telegram_dispatcher,
                controller=controller,
                store=store,
                recipients=startup_context.get("recipient_roles", {}),
                cat_notification_preferences=startup_context.get("cat_notification_preferences"),
                dashboard_network=args.dashboard_network,
                go2rtc_status=startup_context.get("go2rtc_status"),
                tailscale_status=startup_context.get("tailscale_status"),
                status_service=startup_context.get("status_service"),
                privacy_service=startup_context.get("privacy_service"),
                host_restart=startup_context.get("host_restart"),
                live_seconds=float(startup_context.get("live_seconds", 15.0)),
                ring_retention_seconds=float(startup_context.get("ring_retention_seconds", 60.0)),
                logger=log_callback,
            )
            from meowkeeper.core.identity.pet_registry import PetRegistry

            telegram_feedback_poller = TelegramFeedbackPoller(
                notifier, store, feedback_chat_ids, command_router=telegram_command_router,
                pet_registry=PetRegistry(Path(args.event_directory).resolve().parent / "pets"),
                preferences=startup_context.get("cat_notification_preferences"),
                logger=log_callback,
            )
            telegram_feedback_poller.start()
            telegram_command_router.report_pending_restart_results()
            print("ℹ️ Telegram 單一更新接收器已啟動；命令與貓咪正誤回饋共用同一個 poller。")
        elif startup_context.get("telegram_enabled") and notifier is not None:
            if callable(log_callback):
                log_callback("ℹ️ 貓咪回饋按鈕需私人 Chat ID；目前通知對象不含可回覆的私人聊天。")
        if args.dev:
            print(f"⚠️ 開發模式：Dashboard bind={args.host}；go2rtc 仍只監聽 localhost。")
        if startup_context.get("enabled") and notifier is not None and chat_ids:
            try:
                notice = _build_startup_notice(
                    status_service=startup_context.get("status_service"),
                    dashboard_snapshot=args.dashboard_network.snapshot(),
                    source_type=str(startup_context.get("source_type") or "unknown"),
                    source_configured=bool(startup_context.get("source_configured")),
                )
                dispatcher = telegram_dispatcher or NotificationDispatcher(max_pending=max(1, len(chat_ids)))
                for chat_id in chat_ids:
                    # Capture only the selected opaque Chat ID and the already
                    # redacted message; Bot Token never enters this closure.
                    dispatcher.submit(
                        lambda selected_chat_id=chat_id, text=notice: notifier.send_message(selected_chat_id, text)
                    )
                print(f"ℹ️ 已將 Host 啟動摘要排入 Telegram 通知佇列（{len(chat_ids)} 位通知對象）。")
            except Exception as exc:  # noqa: BLE001 - notification must not stop Host
                print(f"⚠️ Host 啟動摘要未送出：{type(exc).__name__}。", file=sys.stderr)
        if args.open_browser:
            webbrowser.open(base_url + "/")
        while thread.is_alive():
            thread.join(timeout=0.25)
    except KeyboardInterrupt:
        print("ℹ️ 收到停止要求，正在安全關閉 Host。")
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        if telegram_feedback_poller is not None:
            telegram_feedback_poller.close(wait=True)
        if telegram_command_router is not None:
            telegram_command_router.close(wait=True)
        controller.close()
        if telegram_dispatcher is not None:
            telegram_dispatcher.shutdown(wait=True)
        store.close()
        instance_lock.release()
    if restart_requested:
        try:
            restart_argv = [item for item in sys.argv if item != "--disable-monitor"]
            if not restart_monitor_requested:
                restart_argv.append("--disable-monitor")
            os.execv(sys.executable, [sys.executable, *restart_argv])
        except OSError as exc:
            print(f"Host 重新啟動失敗：{type(exc).__name__}。", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
