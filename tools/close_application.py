"""使用者明確選擇 Tray 完整關閉後的獨立 helper。"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meowkeeper.tray.client import TrayApiClient, TrayApiError
from meowkeeper.tray.shutdown import close_owned_processes


def main() -> int:
    parser = argparse.ArgumentParser(description="完整關閉此份喵喵守護者+，不刪除資料。")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--already-closing", action="store_true")
    args = parser.parse_args()
    if args.root.resolve() != ROOT:
        raise ValueError("關閉範圍必須是此 helper 所屬的應用目錄。")
    client = TrayApiClient.from_environment(base_url=args.base_url)
    installed = ROOT == (Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "MeowKeeperPlus/App").resolve()
    if installed and os.name == "nt" and not ctypes.windll.shell32.IsUserAnAdmin():
        parameters = subprocess.list2cmdline([str(Path(__file__).resolve()), "--root", str(ROOT), "--base-url", args.base_url])
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, parameters, str(ROOT), 0)
        if result <= 32:
            ctypes.windll.user32.MessageBoxW(0, "管理員授權未完成，程式仍在執行，未宣稱完整關閉。", "喵喵守護者+", 0x10)
            return 1
        return 0
    try:
        graceful = args.already_closing
        # Prefer authenticated graceful shutdown; never weaken Dashboard auth.
        if client.token and not args.already_closing:
            try:
                graceful = client.shutdown().get("status") == "closing"
            except TrayApiError:
                pass
        if installed and not graceful:
            scheduler = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/schtasks.exe"
            # Only end tasks whose XML identifies this exact application's entry.
            for task, entry in (("MeowKeeperPlus Core", "run_host.py"), ("MeowKeeperPlus Tray", "run_tray.py")):
                result = subprocess.run([str(scheduler), "/Query", "/TN", task, "/XML"],
                                        capture_output=True, timeout=10, check=False, shell=False)
                text = result.stdout.decode("utf-16" if result.stdout.startswith(b"\xff\xfe") else "utf-8", errors="replace")
                if result.returncode == 0 and str(ROOT / "tools" / entry).lower() in text.lower():
                    subprocess.run([str(scheduler), "/End", "/TN", task], capture_output=True,
                                   timeout=10, check=False, shell=False)
        close_owned_processes(ROOT, grace_seconds=30 if graceful else 0)
        return 0
    except (TrayApiError, OSError, subprocess.SubprocessError) as exc:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(0, "完整關閉未完成；請保留 Tray 並檢查管理員權限。\n" + type(exc).__name__, "喵喵守護者+", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
