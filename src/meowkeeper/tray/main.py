"""Windows System Tray adapter for the localhost MeowKeeper+ Host."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meowkeeper.runtime_paths import active_data_root_from_status

from .client import TrayApiClient, TrayApiError
from .single_instance import SingleInstanceLock
from .windows_menu import responsive_icon_class

MENU_LABELS = (
    "開啟喵喵守護者+",
    "目前狀態",
    "截圖（開啟即時畫面）",
    "保存影片（開啟即時畫面）",
    "小米攝影機設定",
    "啟動背景核心",
    "停止背景核心",
    "重新啟動背景核心",
    "開啟事件資料夾",
    "查看日誌",
    "匯出診斷資料",
    "完整關閉喵喵守護者+",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="喵喵守護者+ Windows System Tray")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-env", default="MEOWKEEPER_WEB_TOKEN")
    parser.add_argument("--action", choices=("open", "health", "status", "start", "stop", "restart"), default="open")
    parser.add_argument("--confirm", action="store_true", help="確認停止或重啟背景核心")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--open-dashboard", action="store_true", help="開啟 Dashboard 後繼續啟動 Tray")
    parser.add_argument("--smoke", action="store_true", help="以真實 pystray backend 啟動並可定時退出")
    parser.add_argument("--exit-after-seconds", type=float, default=None)
    parser.add_argument("--verify-menu", action="store_true", help="檢查固定選單契約後離開")
    parser.add_argument("--status-json", action="store_true", help="輸出安全的 Host／Tray 狀態 JSON")
    parser.add_argument("--background", action="store_true", help="由 launcher 啟動時不等待使用者輸入")
    parser.add_argument(
        "--lock-root",
        type=Path,
        default=Path(os.getenv("MEOWKEEPER_LOCK_ROOT") or Path(__file__).resolve().parents[3]),
        help="單一實例診斷檔根目錄。",
    )
    return parser


def _safe_print(value: object, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is None:  # pythonw.exe has no console streams.
        return
    try:
        print(value, file=stream, flush=True)
    except (OSError, ValueError):
        return


def _asset_path(status: str = "ready") -> Path:
    root = Path(__file__).resolve().parents[3]
    candidate = root / "assets" / "tray" / f"meowkeeper-earth-{status}.ico"
    fallback = root / "assets" / "tray" / "meowkeeper-earth-ready.ico"
    return candidate if candidate.is_file() else fallback


def _load_image(status: str = "ready") -> Any:
    try:
        from PIL import Image, ImageDraw

        path = _asset_path(status)
        if path.is_file():
            return Image.open(path)
        image = Image.new("RGBA", (64, 64), (73, 55, 40, 255))
        ImageDraw.Draw(image).ellipse((10, 10, 54, 54), fill=(206, 214, 164, 255))
        return image
    except ImportError as exc:  # pragma: no cover - dependency bootstrap covers this
        raise TrayApiError("缺少 Pillow，無法建立 System Tray 圖示。") from exc


def _status_kind(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "error"
    status = str(payload.get("status", "")).lower()
    if status in {"ok", "running", "ready", "healthy"}:
        return "ready"
    if status in {"stopped", "idle", "not_configured", "waiting_for_source"}:
        return "waiting"
    return "error"


def status_payload(client: TrayApiClient) -> dict[str, Any]:
    try:
        health = client.health()
    except TrayApiError as exc:
        return {"status": "error", "message_zh_tw": str(exc)}
    if not client.token:
        return {"status": "ok", "health": health,
                "core": {"status": health.get("core_status", "unknown")},
                "limited": True, "message_zh_tw": "Tray 未獲詳細 API 授權；請登入 Dashboard 查看。"}
    try:
        status = client.status()
    except TrayApiError as exc:
        return {"status": "degraded", "health": health, "message_zh_tw": str(exc)}
    return {"status": "ok", "health": health, "core": status}


def _active_data_root_for_action(client: TrayApiClient, app_root: Path) -> Path:
    """Use the live Host's verified events path for local folder actions."""

    try:
        status = client.status()
    except TrayApiError as exc:
        raise TrayApiError("無法確認背景核心正在使用的資料夾；請先登入 Dashboard 後再試。") from exc
    data_root = active_data_root_from_status(app_root, status)
    if data_root is None:
        raise TrayApiError("背景核心未回報可驗證的資料夾位置；為避免開錯資料，沒有開啟其他資料夾。")
    return data_root


def verify_menu_contract() -> dict[str, Any]:
    return {"status": "ok", "item_count": len(MENU_LABELS), "items": list(MENU_LABELS)}


def status_summary(payload: dict[str, Any]) -> str:
    """只取白名單狀態，絕不把來源網址或診斷全文放進通知。"""
    labels = {
        "running": "運行中", "ok": "正常", "ready": "已就緒",
        "stopped": "已停止", "idle": "待命", "disabled": "未啟用",
        "connected": "已連線", "streaming": "取流中", "waiting": "等待中",
        "starting": "啟動中", "failed": "失敗", "error": "異常",
        "reconnecting": "重新連線中", "not_configured": "尚未設定",
        "waiting_for_source": "等待來源", "CPU": "CPU", "cpu": "CPU",
        "cuda": "CUDA", "PyTorch CUDA": "PyTorch CUDA", "ONNX Runtime CUDA": "ONNX Runtime CUDA",
        "OpenVINO GPU": "Intel 內顯 GPU",
    }
    core = payload.get("core") or {}
    metrics = core.get("metrics") or {}
    def label(value: Any) -> str:
        return labels.get(str(value), "尚未確認")
    return "\n".join((
        "Dashboard：" + ("可連線" if payload.get("health", {}).get("status") == "ok" else "尚未確認"),
        "背景監控：" + label(core.get("status")),
        "影音來源：" + label(metrics.get("source", {}).get("status")),
        "AI 後端：" + label(metrics.get("ai", {}).get("backend")),
        "Telegram：" + label(metrics.get("telegram", {}).get("status")),
        "Dashboard 正常不代表攝影機已接通；詳情請查看總覽。",
        "詳細狀態未授權；請登入 Dashboard。" if payload.get("limited") else "",
    ))


def _message_box(message: str, *, error: bool = False) -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "喵喵守護者+", 0x10 if error else 0x40)
    else:
        _safe_print(message, error=error)


def run_pystray(
    client: TrayApiClient,
    *,
    exit_after_seconds: float | None = None,
    notify: bool = True,
    open_dashboard: bool = False,
) -> int:
    try:
        import pystray
    except ImportError:
        _safe_print("❌ 缺少 pystray；請由專案 launcher 自動補齊。", error=True)
        return 2

    root = Path(__file__).resolve().parents[3]
    image = _load_image("ready")
    icon: Any = None
    action_lock = threading.Lock()
    def notice(message: str) -> None:
        _safe_print(message)
        try:
            icon.notify(message, "喵喵守護者+")
        except (AttributeError, OSError, RuntimeError):
            pass

    def open_page(path: str, message: str) -> None:
        if not client.open_dashboard_page(path):
            raise TrayApiError("無法開啟瀏覽器；請手動開啟本機 Dashboard。")
        notice(message)

    def open_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:  # pragma: no cover - Windows release path
            webbrowser.open(path.as_uri())

    def invoke(action: str) -> None:
        try:
            if not client.token and action in {"start", "stop", "restart", "diagnostics"}:
                open_page("/diagnostics" if action == "diagnostics" else "/dashboard",
                          "Tray 沒有控制 API 授權；已開啟控制台，請登入後操作。尚未執行此要求。")
                return
            if action == "open":
                open_page("/", "已送出開啟 Dashboard 的要求；若未登入，請先登入。")
            elif action == "status":
                notice("正在查詢目前狀態…")
                _message_box(status_summary(status_payload(client)))
            elif action in {"snapshot", "recent"}:
                open_page("/live", "已開啟即時畫面；請按截圖或保存前 15 秒。此選單尚未保存檔案。")
            elif action == "xiaomi":
                open_page("/cameras/xiaomi/setup", "已開啟小米設定；本操作不會重新登入或切換來源。")
            elif action == "events_folder":
                data_root = _active_data_root_for_action(client, root)
                open_directory(data_root / "events")
                notice("已送出開啟事件資料夾的要求。")
            elif action == "logs_folder":
                open_page("/logs", "已開啟 Dashboard 日誌頁；需要目前的登入狀態。")
            elif action == "diagnostics":
                notice("正在匯出診斷資料…")
                data_root = _active_data_root_for_action(client, root)
                output = data_root / "reports" / f"diagnostics-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(client.diagnostics(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                notice("診斷資料已匯出。")
                _message_box(f"已匯出診斷資料：\n{output}\n請勿公開含私人資料的報告。")
            elif action in {"start", "stop", "restart"}:
                name = {"start": "啟動監控", "stop": "停止監控", "restart": "重新啟動監控"}[action]
                notice(f"正在{name}…")
                result = client.start() if action == "start" else getattr(client, action)(confirm=True)
                if isinstance(result, dict) and result.get("status") in {"error", "failed"}:
                    raise TrayApiError("監控操作未完成；請查看 Dashboard 診斷。")
                summary = status_summary(status_payload(client))
                notice(f"{name}要求已送出。\n{summary}")
                _message_box(f"{name}要求已送出。\n{summary}")
            elif action == "quit_all":
                import ctypes

                confirmed = ctypes.windll.user32.MessageBoxW(
                    0, "將停止監控、通知、Dashboard、所屬媒體程序及 Tray。\n資料不會刪除；再次啟動後才恢復監控。是否完整關閉？",
                    "完整關閉喵喵守護者+", 0x00000004 | 0x00000030,
                ) == 6
                if not confirmed:
                    return
                # The helper outlives this Tray and can request UAC only when
                # the user chooses this explicit full-shutdown menu item.
                python = root / "runtime/python/python.exe" if _is_installed_runtime(root) else root / ".venv/Scripts/python.exe"
                subprocess.Popen(
                    [str(python), str(root / "tools/close_application.py"), "--root", str(root),
                     "--base-url", client.base_url], cwd=str(root), shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                notice("正在完整關閉；成功後圖示與 Dashboard 將消失。若需要 UAC，請核准或取消。")
        except (TrayApiError, OSError) as exc:
            message = str(exc) if isinstance(exc, TrayApiError) else "檔案或程序操作失敗；請確認權限與資料夾是否可用。"
            notice("操作未完成：" + message)
            _message_box("操作未完成：\n" + message, error=True)

    def callback(action: str) -> Callable[[Any, Any], None]:
        def dispatch(_icon: Any, _item: Any) -> None:
            # pystray invokes callbacks on the native Windows message loop.
            # Never perform network, filesystem or confirmation waits there.
            if not action_lock.acquire(blocking=False):
                notice("正在處理上一項操作；請完成已開啟的提示視窗後再試。")
                return
            def work() -> None:
                try:
                    invoke(action)
                finally:
                    action_lock.release()
            threading.Thread(target=work, name="meowkeeper-tray-action", daemon=True).start()
        return dispatch

    menu = pystray.Menu(
        pystray.MenuItem(MENU_LABELS[0], callback("open")),
        pystray.MenuItem(MENU_LABELS[1], callback("status")),
        pystray.MenuItem(MENU_LABELS[2], callback("snapshot")),
        pystray.MenuItem(MENU_LABELS[3], callback("recent")),
        pystray.MenuItem(MENU_LABELS[4], callback("xiaomi")),
        pystray.MenuItem(MENU_LABELS[5], callback("start")),
        pystray.MenuItem(MENU_LABELS[6], callback("stop")),
        pystray.MenuItem(MENU_LABELS[7], callback("restart")),
        pystray.MenuItem(MENU_LABELS[8], callback("events_folder")),
        pystray.MenuItem(MENU_LABELS[9], callback("logs_folder")),
        pystray.MenuItem(MENU_LABELS[10], callback("diagnostics")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(MENU_LABELS[11], callback("quit_all")),
    )
    icon_class = responsive_icon_class(pystray.Icon) if os.name == "nt" else pystray.Icon
    icon = icon_class("MeowKeeperPlus", image, "喵喵守護者+：啟動中", menu)

    def refresh() -> None:
        previous_kind = None
        while getattr(icon, "_meow_running", True):
            payload = status_payload(client)
            kind = _status_kind(payload.get("core") if isinstance(payload.get("core"), dict) else payload)
            try:
                if kind == previous_kind:
                    time.sleep(2.0)
                    continue
                title = {
                    "ready": "喵喵守護者+：核心正常",
                    "waiting": "喵喵守護者+：等待來源",
                    "error": "喵喵守護者+：需要檢查",
                }[kind]
                if hasattr(type(icon), "queue_status"):
                    icon.queue_status(kind, title, _load_image)
                else:
                    icon.title = title
                    icon.icon = _load_image(kind)
                previous_kind = kind
                # The menu is static. Rebuilding it while TrackPopupMenu is
                # open can interfere with native right-click interaction.
            except (OSError, RuntimeError):
                return
            time.sleep(2.0)

    icon._meow_running = True
    refresh_thread = threading.Thread(target=refresh, name="meowkeeper-tray-status", daemon=True)
    refresh_thread.start()
    if notify:
        try:
            icon.notify("喵喵守護者+ 已啟動，點擊圖示可開啟 Dashboard。", "喵喵守護者+")
        except (AttributeError, OSError, RuntimeError):
            pass
    if open_dashboard:
        try:
            client.open_dashboard()
        except TrayApiError:
            pass
    if exit_after_seconds is not None:
        if exit_after_seconds <= 0:
            raise ValueError("--exit-after-seconds 必須大於零。")
        threading.Timer(exit_after_seconds, icon.stop).start()
    try:
        icon.run()
    finally:
        icon._meow_running = False
    return 0


def _is_installed_runtime(root: Path) -> bool:
    return (root / "build_manifest.json").is_file() and (root / "runtime" / "python" / "python.exe").is_file()


def _is_system_install(root: Path) -> bool:
    program_data = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    return root.resolve() == (program_data / "MeowKeeperPlus" / "App").resolve()


def ensure_host(client: TrayApiClient, *, interactive: bool = True) -> bool:
    if client.wait_for_health(timeout_seconds=1.0):
        return True
    root = Path(__file__).resolve().parents[3]
    if _is_installed_runtime(root):
        if _is_system_install(root):
            # The installed Core is owned by Task Scheduler/SYSTEM. Reuse that
            # lifecycle rather than starting an unprivileged duplicate Host.
            scheduler = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "schtasks.exe"
            try:
                result = subprocess.run(
                    [str(scheduler), "/Run", "/TN", "MeowKeeperPlus Core"],
                    capture_output=True, timeout=15, check=False, shell=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                result = None
            if result is None or result.returncode != 0:
                _safe_print("❌ 背景核心排程啟動失敗；請檢查 Windows 工作排程器。", error=True)
                return False
        else:
            python = root / "runtime" / "python" / "python.exe"
            command = [str(python), str(root / "tools" / "run_host.py"), "--no-open-browser"]
            try:
                subprocess.Popen(
                    command, cwd=str(root), shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                _safe_print("❌ 無法啟動可攜版背景核心。", error=True)
                return False
        if client.wait_for_health(timeout_seconds=45.0):
            return True
        _safe_print("❌ 背景核心未在時限內就緒；Tray 不會另開重複程序。", error=True)
        return False
    answer = "y"
    if interactive:
        try:
            answer = input("喵喵守護者+ Host 尚未啟動，是否啟動？[Y/n] ").strip().lower() or "y"
        except (EOFError, KeyboardInterrupt):
            answer = "n"
    if answer not in {"y", "yes"}:
        return False
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        _safe_print(f"❌ 找不到專案 .venv runtime：{python}", error=True)
        return False
    # Use the same repository launcher as the main entry point so a Tray-only
    # start also establishes the official go2rtc bridge and its singleton
    # lock.  ``--no-tray`` prevents a recursive second Tray.
    command = [str(python), str(root / "tools" / "launcher.py"), "--root", str(root), "--no-browser", "--no-tray"]
    try:
        subprocess.Popen(command, cwd=str(root), shell=False)
    except OSError as exc:
        _safe_print(f"❌ 無法啟動 Host：{type(exc).__name__}", error=True)
        return False
    # Model loading can legitimately take longer than a web-only Host start;
    # keep Tray responsive while allowing one bounded startup window.
    if not client.wait_for_health(timeout_seconds=45.0):
        _safe_print("❌ Host 未在逾時內回應 /healthz。", error=True)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verify_menu:
        _safe_print(json.dumps(verify_menu_contract(), ensure_ascii=False, indent=2))
        return 0
    try:
        client = TrayApiClient.from_environment(base_url=args.base_url, token_env=args.token_env)
        if args.status_json:
            _safe_print(json.dumps(status_payload(client), ensure_ascii=False, indent=2))
            return 0
        if args.action != "open":
            result = {
                "health": client.health,
                "status": client.status,
                "start": client.start,
                "stop": lambda: client.stop(confirm=args.confirm),
                "restart": lambda: client.restart(confirm=args.confirm),
            }[args.action]()
            _safe_print(json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result)
            return 0
        os.environ["MEOWKEEPER_LOCK_ROOT"] = str(args.lock_root)
        with SingleInstanceLock(root=args.lock_root):
            # Acquire the Tray mutex before attempting to start a Host.  A
            # second launch must not race into ``ensure_host`` and create a
            # second runtime before it discovers the existing icon.
            if not ensure_host(client, interactive=not args.background):
                return 2
            if args.open_dashboard and not args.no_browser:
                client.open_dashboard()
            return run_pystray(
                client,
                exit_after_seconds=args.exit_after_seconds,
                notify=True,
                open_dashboard=False,
            )
    except RuntimeError as exc:
        # A second launch should reuse the first Tray rather than create a
        # second icon.  The existing Host remains untouched.
        if "Tray 已經在執行" in str(exc):
            _safe_print("ℹ️ MeowKeeper+ Tray 已在執行，沿用現有圖示。")
            if args.open_dashboard and not args.no_browser:
                client.open_dashboard()
            return 0
        _safe_print(f"❌ Tray 啟動失敗：{exc}", error=True)
        return 2
    except (ValueError, TrayApiError) as exc:
        _safe_print(f"❌ Tray 操作失敗：{exc}", error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
