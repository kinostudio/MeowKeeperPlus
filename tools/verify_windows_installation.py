"""Verify an installed MeowKeeper+ Windows runtime without exposing secrets."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - portable builder requires psutil
    psutil = None


TASK_NAMES = ("MeowKeeperPlus Core", "MeowKeeperPlus Tray")


def _dashboard_port(data_root: Path) -> int:
    config = data_root / "config.json"
    if not config.is_file():
        return 8000
    try:
        payload = json.loads(config.read_text(encoding="utf-8-sig"))
        value = payload.get("dashboard", {}).get("port", 8000)
        port = int(value)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 8000
    return port if 1 <= port <= 65535 else 8000


def _task_exists(name: str) -> bool:
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe"
    if not executable.is_file():
        return False
    result = subprocess.run(
        [str(executable), "/Query", "/TN", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=15,
        shell=False,
    )
    return result.returncode == 0


def _process_snapshot(root: Path) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "host_count": 0,
        "tray_count": 0,
        "go2rtc_count": 0,
        "all_python_from_embedded_runtime": True,
        "pids": {"host": [], "tray": [], "go2rtc": []},
    }
    if psutil is None:
        snapshot["status"] = "psutil_missing"
        return snapshot
    root_text = str(root.resolve()).casefold()
    expected_python = str((root / "runtime" / "python" / "python.exe").resolve()).casefold()
    expected_pythonw = str((root / "runtime" / "python" / "pythonw.exe").resolve()).casefold()
    expected_go2rtc_root = str((root / "vendor" / "go2rtc").resolve()).casefold()
    for process in psutil.process_iter(["pid", "exe", "cmdline"]):
        try:
            executable = str(process.info.get("exe") or "").casefold()
            command = " ".join(process.info.get("cmdline") or []).casefold()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
        if root_text not in command and root_text not in executable:
            continue
        if "run_host.py" in command:
            snapshot["host_count"] += 1
            snapshot["pids"]["host"].append(process.pid)
            snapshot["all_python_from_embedded_runtime"] &= executable in {
                expected_python,
                expected_pythonw,
            }
        elif "run_tray.py" in command:
            snapshot["tray_count"] += 1
            snapshot["pids"]["tray"].append(process.pid)
            snapshot["all_python_from_embedded_runtime"] &= executable in {
                expected_python,
                expected_pythonw,
            }
        elif expected_go2rtc_root in executable:
            snapshot["go2rtc_count"] += 1
            snapshot["pids"]["go2rtc"].append(process.pid)
    snapshot["status"] = "checked"
    return snapshot


def _health(port: int, deadline: float) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}/healthz"
    last_error = "not_checked"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return {"status": "passed", "http_status": 200, "url": url}
                last_error = f"http_{response.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = type(exc).__name__
        time.sleep(0.5)
    return {"status": "failed", "http_status": None, "url": url, "error_type": last_error}


def verify_installation(
    root: Path,
    data_root: Path,
    *,
    wait_seconds: float = 0.0,
    task_names: tuple[str, str] = TASK_NAMES,
    startup_links: tuple[Path, ...] = (),
) -> dict[str, Any]:
    package = root.resolve()
    state = data_root.resolve()
    tasks = {name: _task_exists(name) for name in task_names}
    links = {str(path): path.is_file() for path in startup_links}
    port = _dashboard_port(state)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    health = _health(port, deadline) if wait_seconds > 0 else _health(port, time.monotonic() + 0.01)
    processes = _process_snapshot(package)
    passed = (
        (all(tasks.values()) if not startup_links else all(links.values()))
        and health["status"] == "passed"
        and processes["host_count"] == 1
        and processes["tray_count"] == 1
        and processes["all_python_from_embedded_runtime"]
    )
    return {
        "status": "passed" if passed else "failed",
        "application_root": str(package),
        "data_root": str(state),
        "tasks": tasks,
        "startup_links": links,
        "dashboard": health,
        "processes": processes,
    }


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[1]
    default_data = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "MeowKeeperPlus" / "Data"
    parser = argparse.ArgumentParser(description="驗證 MeowKeeper+ Windows 安裝、排程、程序與 Dashboard。")
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--data-root", type=Path, default=default_data)
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    parser.add_argument("--core-task-name", default=TASK_NAMES[0])
    parser.add_argument("--tray-task-name", default=TASK_NAMES[1])
    parser.add_argument(
        "--startup-link",
        action="append",
        type=Path,
        default=[],
        help="目前使用者模式的 Startup 捷徑；提供時以捷徑取代排程作為安裝條件。",
    )
    args = parser.parse_args(argv)
    result = verify_installation(
        args.root,
        args.data_root,
        wait_seconds=args.wait_seconds,
        task_names=(args.core_task_name, args.tray_task_name),
        startup_links=tuple(args.startup_link),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
