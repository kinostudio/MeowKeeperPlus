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
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from meowkeeper.app import delta_update as delta


def prepare(app: Path, downloads: Path, payload: Path, version: str, fetch) -> dict:
    """No ZIP request until signature, exact signed baseline and files are valid."""
    base_url = f"https://github.com/kinostudio/MeowKeeperPlus/releases/download/{version}/"
    envelope_path = downloads / "update-manifest.json"
    fetch(base_url + "update-manifest.json", envelope_path, 65536)
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
        prepare(app, downloads, payload, args.version, downloader._download)
        if not args.apply:
            print("差量下載與簽章驗證完成，尚未停止監控或安裝。")
            return 0
        request = data / "updates/apply-request.json"
        if request.exists():
            raise ValueError("已有更新要求，拒絕重複套用。")
        request.parent.mkdir(parents=True, exist_ok=True)
        prepared = request.parent / ("prepared-" + uuid.uuid4().hex)
        prepared.mkdir()
        for name in ("package.zip", "update-manifest.json"):
            shutil.copy2(downloads / name, prepared / name)
        request_id = uuid.uuid4().hex
        with request.open("x", encoding="utf-8") as stream:
            json.dump({"schema": 1, "request_id": request_id, "prepared_id": prepared.name, "version": args.version}, stream)
        worker = payload / "scripts/Apply-MeowKeeperPlusUpdate.ps1"
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(worker),
                        "-VerifierRoot", str(payload)], check=True, timeout=3600, shell=False)
        status = json.loads((updater / "hotfix-status.json").read_text(encoding="utf-8"))
        if status.get("request_id") != request_id or status.get("status") != "applied":
            raise ValueError("差量更新未完成；請查看更新狀態，失敗現場與備份仍保留。")
        print("差量更新完成，簽章／完整性與啟動檢查通過。")
        return 0
    except Exception as error:  # noqa: BLE001 - network/OS errors must not expose signed redirect URLs.
        print("差量更新停止（" + type(error).__name__ + "）；不會改抓完整包。")
        if isinstance(error, ValueError):
            print(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
