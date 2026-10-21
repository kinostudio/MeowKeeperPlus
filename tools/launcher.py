"""MeowKeeper+ Windows launcher and repository-local runtime bootstrap.

The CMD and PowerShell files are intentionally thin.  This module owns the
runtime checks, host single-instance decision, health wait and optional tray
startup so the same rules are used from every working directory.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_IMPORTS = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "jinja2": "jinja2",
    "multipart": "multipart",
    "itsdangerous": "itsdangerous",
    "httpx": "httpx",
    "pystray": "pystray",
    "PIL": "PIL",
    "ultralytics": "ultralytics",
    "cv2": "cv2",
    "numpy": "numpy",
}


@dataclass(frozen=True)
class RuntimeProbe:
    missing: tuple[str, ...]
    versions: dict[str, str]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def runtime_python(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "python.exe"


def _version(module: Any) -> str:
    try:
        from importlib.metadata import version

        return version(module.__name__)
    except Exception:  # noqa: BLE001 - version metadata is diagnostic only
        return "installed"


def probe_runtime() -> RuntimeProbe:
    missing: list[str] = []
    versions: dict[str, str] = {}
    for name, import_name in REQUIRED_IMPORTS.items():
        try:
            module = importlib.import_module(import_name)
        except Exception:  # noqa: BLE001 - probe must report every missing import
            missing.append(name)
            continue
        versions[name] = _version(module)
    return RuntimeProbe(tuple(missing), versions)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"[{_utc_now()}] {text.rstrip()}\n")


def bootstrap_runtime(root: Path, *, allow_install: bool = True) -> RuntimeProbe:
    """Install only the declared extras into the existing repository venv."""

    python = runtime_python(root)
    if not python.is_file():
        raise RuntimeError(f"找不到專案 runtime：{python}。請由建置流程準備 .venv。")
    # Ultralytics creates a settings directory on import.  Keep it inside the
    # repository data boundary instead of attempting to write a protected
    # roaming profile during a background/service launch.
    os.environ.setdefault("YOLO_CONFIG_DIR", str(root / "data" / "ultralytics"))
    probe = probe_runtime()
    log_path = root / "reports" / "phase3b" / "runtime-bootstrap.log"
    if not probe.missing:
        _append_log(log_path, "runtime import probe passed; pip install skipped")
        return probe
    if not allow_install:
        raise RuntimeError("缺少必要 runtime 套件：" + ", ".join(probe.missing))
    command = [str(python), "-m", "pip", "install", "-e", ".[runtime,web,tray]", "--disable-pip-version-check"]
    _append_log(log_path, "missing imports: " + ", ".join(probe.missing))
    _append_log(log_path, "executing repository venv pip bootstrap")
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _append_log(log_path, f"pip failed: {type(exc).__name__}")
        raise RuntimeError(f"必要套件自動補齊失敗，詳見 {log_path}") from exc
    # Do not store URLs/tokens from package output; dependency output is kept
    # only for local diagnosis and is redacted for common secret patterns.
    output = (completed.stdout or "") + (completed.stderr or "")
    _append_log(log_path, output[-12000:])
    if completed.returncode != 0:
        raise RuntimeError(f"必要套件自動補齊失敗（exit {completed.returncode}），詳見 {log_path}")
    probe = probe_runtime()
    if probe.missing:
        raise RuntimeError("安裝後仍缺少必要 runtime 套件：" + ", ".join(probe.missing))
    _append_log(log_path, "runtime import probe passed after bootstrap")
    return probe


def _health_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/healthz"


def check_health(base_url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(_health_url(base_url), timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_for_health(base_url: str, *, timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if check_health(base_url):
            return True
        time.sleep(0.3)
    return False


def discover_browser() -> list[Path]:
    """Return existing browser candidates without installing or changing one."""

    candidates = [
        os.environ.get("ProgramFiles(x86)", "") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("ProgramFiles", "") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("ProgramFiles(x86)", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("ProgramFiles", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Google\Chrome\Application\chrome.exe",
    ]
    found: list[Path] = []
    for value in candidates:
        if value and Path(value).is_file():
            found.append(Path(value))
    for name in ("msedge.exe", "chrome.exe"):
        located = shutil_which(name)
        if located is not None and located not in found:
            found.append(located)
    return found


def shutil_which(name: str) -> Path | None:
    import shutil

    value = shutil.which(name)
    return Path(value) if value else None


def open_dashboard(base_url: str) -> bool:
    """Use the system browser when one is available; return launch result."""

    url = base_url.rstrip("/") + "/"
    browsers = discover_browser()
    if browsers:
        try:
            subprocess.Popen([str(browsers[0]), url], shell=False)
            return True
        except OSError:
            pass
    return bool(webbrowser.open(url))


def _verify_runtime_files(root: Path) -> None:
    from meowkeeper.runtime import bundled_binary, validate_model_file

    validate_model_file()
    if not bundled_binary("ffmpeg") or not bundled_binary("ffprobe"):
        raise RuntimeError("找不到專案內 vendor\\ffmpeg 的 ffmpeg 或 ffprobe。")


def _start_host(root: Path, *, base_url: str, forwarded: list[str]) -> subprocess.Popen[str]:
    python = runtime_python(root)
    host = root / "tools" / "run_host.py"
    command = [str(python), str(host), "--no-open-browser", *forwarded]
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout = (logs / "host.log").open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command, cwd=str(root), stdout=stdout, stderr=subprocess.STDOUT, shell=False, text=True
        )
    finally:
        # Popen inherits the handle; the parent launcher must not keep an
        # extra file descriptor open for the lifetime of the Host.
        stdout.close()
    return process


def run_launcher(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root / "src"))
    from meowkeeper.app.web.network import (
        DashboardNetworkService,
        listener_bind_matches,
        resolve_dashboard_config,
    )
    from meowkeeper.integrations.go2rtc.process import Go2RtcProcess
    from meowkeeper.process_guard import (
        CrossProcessLock,
        is_project_python,
        iter_meowkeeper_processes,
        safe_stop_legacy_processes,
    )
    from meowkeeper.runtime_paths import default_dashboard_config_path, default_data_root
    data_root = default_data_root(root)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(data_root / "ultralytics"))
    config_path = (
        Path(args.dashboard_config)
        if args.dashboard_config
        else default_dashboard_config_path(root, data_root)
    )
    if not config_path.is_absolute():
        config_path = root / config_path
    try:
        dashboard_config = resolve_dashboard_config(
            config_path,
            port=args.port,
            lan_access_enabled=args.lan_access_enabled,
        )
    except (OSError, ValueError) as exc:
        print(f"❌ Dashboard 網路設定不安全或無法讀取：{exc}", file=sys.stderr)
        return 2
    dashboard_network = DashboardNetworkService(dashboard_config, config_path=config_path)
    args.port = dashboard_config.port
    if args.status_json:
        payload = {
            "root": str(root),
            "python": str(runtime_python(root)),
            "runtime": probe_runtime().__dict__,
            "browsers": [str(path) for path in discover_browser()],
            "dashboard": dashboard_network.snapshot(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    try:
        probe = bootstrap_runtime(root, allow_install=True)
        _verify_runtime_files(root)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"❌ 啟動前檢查失敗：{exc}", file=sys.stderr)
        return 2
    health_url = f"http://127.0.0.1:{args.port}"
    network_snapshot = dashboard_network.snapshot()
    display_urls = network_snapshot.get("urls") or [network_snapshot.get("local_url")]
    base_url = str(display_urls[0])
    report = root / "reports" / "phase3b" / "launcher.log"
    _append_log(report, "runtime versions: " + json.dumps(probe.versions, ensure_ascii=False))
    host_process = None
    go2rtc_process = None
    launcher_lock = CrossProcessLock("MeowKeeperPlusLauncher", root=root)
    lock_acquired = launcher_lock.acquire()
    if not lock_acquired and not check_health(health_url) and not wait_for_health(health_url, timeout_seconds=15.0):
        print("❌ 另一個啟動程序正在準備 Host，但 15 秒內未回應 /healthz。", file=sys.stderr)
        return 3
    if lock_acquired:
        try:
            # Only processes whose command line contains this repository and a
            # known MeowKeeper role are eligible.  A generic Python process is
            # never stopped.  Legacy system-Python trees are removed before a
            # new .venv Host is started.
            legacy = [
                item
                for item in iter_meowkeeper_processes(root)
                if item.pid != os.getpid()
                and item.role in {"launcher", "host", "tray"}
                and not is_project_python(item, root)
            ]
            if legacy:
                stopped = safe_stop_legacy_processes(root, keep_pids={os.getpid()})
                _append_log(report, "stopped legacy MeowKeeper processes: " + json.dumps(stopped, ensure_ascii=False))
                if args.verify_launcher:
                    # The legacy-process contract test may be launched by a
                    # system-code-page Python.  Keep its machine-readable
                    # stdout decodable while the normal launcher remains
                    # Traditional Chinese for users.
                    print("Legacy MeowKeeper processes were safely stopped; using repository .venv.")
                else:
                    print("⚠️ 偵測到舊版系統 Python MeowKeeper+ 程序，已依完整命令列安全停止後改用專案 .venv。")
                time.sleep(0.5)
            # Start or reuse the official localhost go2rtc bridge before the
            # Host.  Go2RtcProcess owns its own cross-process lock and never
            # overwrites an existing authenticated session config.
            go2rtc_process = Go2RtcProcess(root)
            go2rtc_status = go2rtc_process.start()
            if not go2rtc_status.healthy:
                _append_log(report, "go2rtc start failed: " + go2rtc_status.message_zh_tw)
                if go2rtc_process.process is not None:
                    go2rtc_process.stop()
                print(f"❌ go2rtc 啟動失敗：{go2rtc_status.message_zh_tw}", file=sys.stderr)
                return 4
            existing_healthy = check_health(health_url)
            if existing_healthy:
                bind_matches = listener_bind_matches(args.port, dashboard_config.bind_host)
                if bind_matches is False:
                    from meowkeeper.process_guard import safe_stop_identified_processes

                    stopped = safe_stop_identified_processes(root, include_go2rtc=False, keep_pids={os.getpid()})
                    _append_log(report, "restarted Host because configured bind changed: " + json.dumps(stopped, ensure_ascii=False))
                    print("ℹ️ 既有 Host 的 bind 與設定不同，已安全停止目前 MeowKeeper+ Host，準備套用新設定。")
                    time.sleep(0.5)
                    existing_healthy = False
                else:
                    host_process = None
                    _append_log(report, "existing healthy .venv Host reused")
            if not existing_healthy:
                forwarded = ["--port", str(args.port), "--dashboard-config", str(config_path)]
                if args.lan_access_enabled is True:
                    forwarded.append("--lan-access")
                elif args.lan_access_enabled is False:
                    forwarded.append("--no-lan-access")
                if args.no_monitor:
                    forwarded.append("--disable-monitor")
                if args.enable_telegram:
                    forwarded.append("--enable-telegram")
                if args.telegram_token_env != "MEOWKEEPER_TELEGRAM_BOT_TOKEN":
                    forwarded.extend(["--telegram-token-env", args.telegram_token_env])
                if args.telegram_chat_ids_env != "MEOWKEEPER_TELEGRAM_CHAT_IDS":
                    forwarded.extend(["--telegram-chat-ids-env", args.telegram_chat_ids_env])
                if args.dev:
                    forwarded.append("--dev")
                host_process = _start_host(root, base_url=base_url, forwarded=forwarded)
                _append_log(report, f"started Host pid={host_process.pid} executable={runtime_python(root)}")
                if not wait_for_health(health_url):
                    if go2rtc_process.process is not None:
                        go2rtc_process.stop()
                    print(f"❌ Host 未在 30 秒內回應 /healthz，請查看 {root / 'logs' / 'host.log'}", file=sys.stderr)
                    return 2
        finally:
            launcher_lock.release()
    if args.verify_launcher:
        if host_process is not None and host_process.poll() is None:
            host_process.terminate()
            try:
                host_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                host_process.kill()
                host_process.wait(timeout=5)
        if go2rtc_process is not None:
            go2rtc_process.stop()
        print(json.dumps({"status": "ok", "health": True, "host_reused": host_process is None}, ensure_ascii=False))
        return 0
    if not args.no_tray:
        tray_python = root / ".venv" / "Scripts" / ("pythonw.exe" if os.name == "nt" else "python")
        tray_script = root / "tools" / "run_tray.py"
        existing_tray = any(
            item.role == "tray" and is_project_python(item, root)
            for item in iter_meowkeeper_processes(root)
        )
        if existing_tray:
            _append_log(report, "existing healthy .venv Tray reused; no second pythonw started")
        elif tray_python.is_file() and tray_script.is_file():
            try:
                subprocess.Popen(
                    [str(tray_python), str(tray_script), "--no-browser"],
                    cwd=str(root),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError as exc:
                _append_log(report, f"tray start failed: {type(exc).__name__}")
    if not args.no_browser and not open_dashboard(base_url):
        print(f"⚠️ 找不到可啟動的瀏覽器，Dashboard 仍可由 {base_url}/ 開啟。")
    if network_snapshot.get("lan_access_enabled") and network_snapshot.get("urls"):
        print("✅ 可用區網網址：" + ", ".join(str(url) for url in network_snapshot["urls"]))
    print(f"✅ 喵喵守護者+ Dashboard 已啟動：{base_url}/")
    # A launcher is a foreground supervisor only when it started Host.  If a
    # healthy Host was already running, the user can return to the shell.
    if host_process is None:
        return 0
    try:
        return host_process.wait()
    except KeyboardInterrupt:
        host_process.terminate()
        return host_process.wait(timeout=10)
    finally:
        # Only a process started by this launcher is stopped here; an existing
        # external authenticated go2rtc has ``process is None`` and is never
        # terminated by MeowKeeper+.
        if go2rtc_process is not None:
            go2rtc_process.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MeowKeeper+ repository-local Windows launcher")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--dashboard-config", default=None)
    lan = parser.add_mutually_exclusive_group()
    lan.add_argument("--lan-access", dest="lan_access_enabled", action="store_true", help="允許同一區網存取 Dashboard。")
    lan.add_argument("--no-lan-access", dest="lan_access_enabled", action="store_false", help="只綁定 localhost。")
    parser.set_defaults(lan_access_enabled=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--no-monitor", action="store_true")
    parser.add_argument(
        "--enable-telegram",
        action="store_true",
        help="明確啟用事件 Telegram 通知；憑證優先讀取 Credential Manager，亦可用環境變數。",
    )
    parser.add_argument("--telegram-token-env", default="MEOWKEEPER_TELEGRAM_BOT_TOKEN")
    parser.add_argument("--telegram-chat-ids-env", default="MEOWKEEPER_TELEGRAM_CHAT_IDS")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--verify-launcher", action="store_true")
    parser.add_argument("--status-json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    return run_launcher(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
