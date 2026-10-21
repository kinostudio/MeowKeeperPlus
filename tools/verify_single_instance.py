"""Windows process/launcher audit for MeowKeeper+ single-instance gates.

The command is read-only by default.  ``--stop-legacy`` only terminates
processes whose executable and command line both identify this repository and
whose Python executable is outside the repository ``.venv``.  It never uses a
name-only ``taskkill``.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.process_guard import (  # noqa: I001
    inspect_process,
    is_project_python,
    iter_meowkeeper_processes,
    port_owner,
    redact_command_line,
    safe_stop_identified_processes,
    safe_stop_legacy_processes,
)


PORTS = (8000, 1984, 8554)
MATCH_TERMS = ("meowkeeper", "run_host.py", "run_tray.py", "start-meowkeeperplus")


def _utc(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _session_id(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        value = wintypes.DWORD()
        if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(value)):
            return int(value.value)
    except (AttributeError, OSError):
        pass
    return None


def _process_payload(root: Path) -> list[dict[str, Any]]:
    records = iter_meowkeeper_processes(root)
    result: list[dict[str, Any]] = []
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        psutil = None  # type: ignore[assignment]
    for record in sorted(records, key=lambda item: item.pid):
        creation = None
        username = None
        if psutil is not None:
            try:
                process = psutil.Process(record.pid)
                creation = _utc(process.create_time())
                username = process.username()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                pass
        result.append(
            {
                "pid": record.pid,
                "parent_pid": record.parent_pid,
                "role": record.role,
                "name": record.name,
                "executable_path": record.executable,
                "command_line": redact_command_line(record),
                "creation_date_utc": creation,
                "session_id": _session_id(record.pid),
                "account": username,
                "project_venv_runtime": is_project_python(record, root),
            }
        )
    return result


def _ports(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for port in PORTS:
        owners = port_owner(port)
        for owner in owners:
            record = inspect_process(owner.pid, root)
            result.append(
                {
                    "port": port,
                    "address": owner.local_address or "unknown",
                    "pid": owner.pid,
                    "role": record.role if record else None,
                    "executable_path": owner.executable,
                    "command_line": redact_command_line(record or owner),
                }
            )
    return result


def _scheduler_matches() -> dict[str, Any]:
    # Use the absolute Windows system utility instead of relying on the
    # caller's PATH.  Sandboxed shells sometimes omit System32 even though the
    # same read-only audit is available to an elevated PowerShell session.
    system_root = Path(os.getenv("SystemRoot", r"C:\\Windows"))
    executable = system_root / "System32" / "schtasks.exe"
    if not executable.is_file():
        return {
            "status": "not_available",
            "reason": "Windows Task Scheduler query utility was not found",
            "items": [],
        }
    command = [str(executable), "/Query", "/FO", "CSV", "/V", "/NH"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "not_checked", "error_type": type(exc).__name__, "items": []}
    rows: list[dict[str, str]] = []
    try:
        parsed = csv.reader(io.StringIO(completed.stdout or ""))
        for row in parsed:
            text = " ".join(row)
            if completed.returncode == 0 and any(term in text.lower() for term in MATCH_TERMS):
                rows.append({"fields": [item[:500] for item in row]})
    except csv.Error:
        return {"status": "error", "items": []}
    if completed.returncode != 0:
        # Some restricted Windows sessions return ERROR_ACCESS_DENIED from
        # schtasks even when the read-only ScheduledTasks PowerShell cmdlets
        # are available.  Retry with the system PowerShell executable and
        # return only task identity fields; action arguments are not emitted.
        powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if powershell.is_file():
            script = (
                "$pattern='(?i)meowkeeper|run_host\\.py|run_tray\\.py|start-meowkeeperplus'; "
                "$tasks=@(Get-ScheduledTask -ErrorAction Stop | "
                "Where-Object { $_.TaskName -match $pattern -or "
                "(($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' ') -match $pattern }); "
                "$rows=@($tasks | ForEach-Object { [pscustomobject]@{name=$_.TaskName; path=$_.TaskPath; state=[string]$_.State} }); "
                "[pscustomobject]@{items=$rows} | ConvertTo-Json -Compress"
            )
            try:
                fallback = subprocess.run(
                    [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                    shell=False,
                )
                if fallback.returncode == 0:
                    payload = json.loads(fallback.stdout or "{}")
                    items = payload.get("items", [])
                    if isinstance(items, dict):
                        items = [items]
                    if not isinstance(items, list):
                        items = []
                    return {"status": "checked", "source": "Get-ScheduledTask", "items": items}
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError, ValueError):
                pass
        return {
            "status": "not_checked",
            "reason": "schtasks and ScheduledTasks PowerShell query were unavailable",
            "exit_code": completed.returncode,
            "items": rows,
        }
    return {"status": "checked", "items": rows}


def _startup_matches() -> list[str]:
    directories = [
        Path(os.getenv("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup",
        Path(os.getenv("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs/Startup",
    ]
    result: list[str] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for item in directory.iterdir():
            if any(term in item.name.lower() for term in MATCH_TERMS):
                result.append(str(item))
    return sorted(result)


def _registry_matches() -> list[dict[str, str]]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    result: list[dict[str, str]] = []
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    )
    for hive, path in locations:
        try:
            with winreg.OpenKey(hive, path) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _ = winreg.EnumValue(key, index)
                    if any(term in f"{name}={value}".lower() for term in MATCH_TERMS):
                        result.append({"path": path, "name": str(name), "value": str(value)[:500]})
        except OSError:
            continue
    return result


def _lock_state(root: Path) -> dict[str, Any]:
    paths = [root / "data" / "locks", Path(os.getenv("TEMP", ""))]
    items: list[dict[str, Any]] = []
    for directory in paths:
        if not directory.is_dir():
            continue
        for item in directory.iterdir():
            if "meowkeeper" not in item.name.lower():
                continue
            entry: dict[str, Any] = {"path": str(item), "is_file": item.is_file(), "size": item.stat().st_size if item.is_file() else None}
            if item.is_file() and item.suffix.lower() == ".json":
                try:
                    payload = json.loads(item.read_text(encoding="utf-8"))
                    pid = int(payload.get("pid", -1))
                    record = inspect_process(pid, root) if pid > 0 else None
                    entry.update(
                        {
                            "owner_pid": pid,
                            "owner_alive": record is not None,
                            "owner_executable_verified": bool(record and str(payload.get("executable", "")).lower() == str(record.executable or "").lower()),
                            "owner_command_line_verified": bool(record and record.role is not None),
                        }
                    )
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    entry["metadata_status"] = "invalid"
            items.append(entry)
    return {"metadata_files": items, "named_mutex": "not_enumerated_by_design", "stale_pid_requires_identity_check": True}


def collect(root: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repository_root": str(root),
        "processes": _process_payload(root),
        "ports": _ports(root),
        "scheduled_tasks": _scheduler_matches(),
        "startup_matches": _startup_matches(),
        "registry_matches": _registry_matches(),
        "lock_state": _lock_state(root),
        "secrets_in_report": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MeowKeeper+ Windows 單一實例唯讀 audit")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "phase3c4" / "processes-before.json")
    parser.add_argument("--stop-legacy", action="store_true", help="只停止已證明為舊版系統 Python 的 MeowKeeper 程序")
    parser.add_argument("--stop-identified", action="store_true", help="停止所有已由 repository 命令列識別的 Host/Tray/launcher")
    parser.add_argument("--include-go2rtc", action="store_true", help="搭配 --stop-identified 停止 repository 官方 go2rtc")
    parser.add_argument("--stopped-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    before = collect(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(before, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.stop_legacy or args.stop_identified:
        if args.stop_identified:
            stopped = safe_stop_identified_processes(root, include_go2rtc=args.include_go2rtc, keep_pids={os.getpid()})
        else:
            stopped = safe_stop_legacy_processes(root, keep_pids={os.getpid()})
        target = args.stopped_output or args.output.with_name("processes-stopped.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        after = collect(root)
        target.write_text(json.dumps({"schema_version": 1, "stopped": stopped, "after": after, "secrets_in_report": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"stopped_count": len(stopped), "output": str(target)}, ensure_ascii=False))
    else:
        print(json.dumps({"process_count": len(before["processes"]), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
