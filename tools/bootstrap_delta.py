"""One-time r116 bridge: signed delta only, using its existing bundled Python."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from meowkeeper.app import delta_update as delta

DELTA_MANIFEST = "update-manifest-delta.json"
LEGACY_DELTA_MANIFEST = "update-manifest.json"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
FINAL_STATES = {"applied", "failed", "rollback_failed"}
BOOTSTRAP_FILES = (
    "scripts/Apply-MeowKeeperPlusUpdate.ps1",
    "tools/apply_verified_update.py",
    "src/meowkeeper/app/delta_update.py",
)


def complete_bootstrap(app: Path, payload: Path, manifest: dict) -> None:
    """A delta omits unchanged files; copy only signed, identical control files."""
    target_path = payload / "build_manifest.json"
    if delta.digest(target_path) != manifest["target_manifest_sha256"]:
        raise ValueError("更新引導的目標清單校驗失敗；尚未啟動安裝。")
    target = delta.records(json.loads(target_path.read_text(encoding="utf-8")))
    base = delta.records(json.loads((app / "build_manifest.json").read_text(encoding="utf-8")))
    for name in BOOTSTRAP_FILES:
        expected = target.get(name)
        if expected is None:
            raise ValueError("更新套件缺少必要引導工具；尚未啟動安裝。")
        destination = payload / name
        if not destination.exists():
            source = app / name
            delta.plain(source)
            if base.get(name) != expected or delta.digest(source) != expected["sha256"]:
                raise ValueError("本機更新引導工具不符合目標簽章；尚未啟動安裝。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        delta.plain(destination)
        if destination.stat().st_size != expected["size_bytes"] or delta.digest(destination) != expected["sha256"]:
            raise ValueError("更新引導工具校驗失敗；尚未啟動安裝。")


def prepare(app: Path, downloads: Path, payload: Path, version: str, fetch) -> dict:
    """No ZIP request until signature, exact signed baseline and files are valid."""
    base_url = f"https://github.com/kinostudio/MeowKeeperPlus/releases/download/{version}/"
    envelope_path = downloads / "update-manifest.json"
    # r121+ publish the delta under its own name so `update-manifest.json`
    # can carry the full signed package that older Dashboards understand.
    try:
        fetch(base_url + DELTA_MANIFEST, envelope_path, 65536)
    except Exception:  # noqa: BLE001 - older releases only published one manifest name.
        if envelope_path.exists():
            envelope_path.unlink()
        fetch(base_url + LEGACY_DELTA_MANIFEST, envelope_path, 65536)
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    manifest = delta.verify_delta(envelope, (app / "configs/update-public.pem").read_bytes())
    if manifest["version"] != version:
        raise ValueError("差量版本不符；不會下載完整包。")
    print("正在核對本機版本與檔案基準；尚未下載差量 ZIP。", flush=True)
    delta.validate_base(app, manifest)
    print("基準與簽章通過；只下載差量檔案。", flush=True)
    archive = downloads / "package.zip"
    fetch(base_url + manifest["asset"], archive, manifest["size"])
    delta.extract_payload(archive, manifest, payload)
    return manifest


def launch_worker(worker: Path, payload: Path, log_path: Path, *, popen=None) -> str:
    """Start the protected worker as its own process tree and return immediately.

    This interpreter lives inside App\\runtime\\python, which the installer must
    stop (with every other bundled-Python process) before it can swap App.  The
    coordinator therefore never waits from inside that runtime: the worker runs
    without a console window and in a new process group, broken away from the
    caller's job when Windows allows it. DETACHED_PROCESS makes PowerShell exit
    without running its -File script, so use CREATE_NO_WINDOW instead.
    """
    popen = popen or subprocess.Popen
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    arguments = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(worker), "-VerifierRoot", str(payload)]
    base_flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    with log_path.open("xb") as log:
        for flags, mode in ((base_flags | CREATE_BREAKAWAY_FROM_JOB, "breakaway"), (base_flags, "new-process-group")):
            try:
                popen(arguments, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                      creationflags=flags, close_fds=True, shell=False, cwd=str(payload))
                return mode
            except OSError:
                if mode == "new-process-group":
                    raise
    raise RuntimeError("worker_not_started")


def wait_for_worker_start(status_path: Path, request_id: str, *, timeout: float = 120.0,
                          sleep=time.sleep, clock=time.monotonic) -> dict | None:
    """Wait only until the worker owns the request; never through the App swap."""
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = None
        if isinstance(status, dict) and status.get("request_id") == request_id:
            return status
        sleep(1.0)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="只下載變更檔案；不會下載完整 runtime／CUDA／模型。")
    parser.add_argument("--version", required=True)
    parser.add_argument("--apply", action="store_true", help="由使用者明確要求套用；會短暫停止監控。")
    args = parser.parse_args()
    try:
        if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
            raise ValueError("請由使用者在主機本機的管理員 PowerShell 執行；不會自行觸發 UAC。")
        if not re.fullmatch(r"r[1-9]\d*", args.version):
            raise ValueError("目標版本格式不正確。")
        install = Path(os.environ["ProgramData"]) / "MeowKeeperPlus"
        app, data, updater = install / "App", install / "Data", install / "Updater"
        for path in (install, app, data, updater):
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("安裝路徑含重新解析點，停止更新。")
        downloads = updater / ("bootstrap-download-" + uuid.uuid4().hex)
        downloads.mkdir()
        subprocess.run(["icacls.exe", str(downloads), "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
                       timeout=30, check=True, capture_output=True, shell=False)
        payload = updater / ("bootstrap-" + uuid.uuid4().hex)
        sys.path.insert(0, str(app / "src"))
        from meowkeeper.app.updates import UpdateManager
        downloader = UpdateManager(app, data)
        manifest = prepare(app, downloads, payload, args.version, downloader._download)
        complete_bootstrap(app, payload, manifest)
        if not args.apply:
            print("差量下載與簽章驗證完成，尚未停止監控或安裝。")
            return 0
        if (data / "updates/apply-request.json").exists():
            raise ValueError("已有更新要求，拒絕重複套用。")
        request = payload / "apply-request.json"
        prepared = request.parent / ("prepared-" + uuid.uuid4().hex)
        prepared.mkdir()
        for name in ("package.zip", "update-manifest.json"):
            shutil.copy2(downloads / name, prepared / name)
        request_id = uuid.uuid4().hex
        with request.open("x", encoding="utf-8") as stream:
            json.dump({"schema": 1, "request_id": request_id, "prepared_id": prepared.name, "version": args.version}, stream)
        worker = payload / "scripts/Apply-MeowKeeperPlusUpdate.ps1"
        mode = launch_worker(worker, payload, payload / "worker-output.log")
        status = wait_for_worker_start(updater / "hotfix-status.json", request_id)
        if status is None:
            raise ValueError("更新工作沒有在時限內開始；尚未切換版本。請保留 Updater 目錄並查看 worker-output.log。")
        if status.get("status") == "failed" or status.get("status") == "rollback_failed":
            raise ValueError(str(status.get("message_zh_tw") or "差量更新未完成；失敗現場與備份仍保留。"))
        print("已啟動受保護的更新工作（" + mode + "）；它不依賴本視窗或舊版 Python，換版時本程式會先結束。", flush=True)
        print("REQUEST_ID=" + request_id, flush=True)
        print("進度與結果：" + str(updater / "hotfix-status.json"), flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - network/OS errors must not expose signed redirect URLs.
        print("差量更新停止（" + type(error).__name__ + "）；不會改抓完整包。")
        if isinstance(error, ValueError):
            print(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
