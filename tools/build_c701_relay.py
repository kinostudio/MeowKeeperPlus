"""Build a secret-free C701 relay bundle from pinned local vendor binaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GO2RTC_SHA256 = "923D57252E8139A69C52E4ACC1E399A640244A8EF457FD9B7267A25847D68F8C"
SCRIPTS = (
    "Start-C701Relay.cmd", "Start-C701Relay.ps1", "Stop-C701Relay.cmd", "Stop-C701Relay.ps1",
    "Test-C701Relay.cmd", "Test-C701Relay.ps1", "Export-C701Pairing.cmd", "Export-C701Pairing.ps1",
    "Watch-C701Relay.ps1", "Manage-C701RelayStartup.ps1", "Set-C701UdpFirewall.ps1",
    "Start-C701Control.ps1", "Set-C701ControlFirewall.ps1", "relay_control_agent.py", "README.md",
)


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest().upper()


def build(output: Path) -> dict[str, object]:
    go2rtc = ROOT / "vendor" / "go2rtc" / "v1.9.14" / "go2rtc.exe"
    ffmpeg = ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
    license_file = ROOT / "vendor" / "ffmpeg" / "LICENSE"
    if digest(go2rtc) != GO2RTC_SHA256:
        raise ValueError("官方 go2rtc 校驗失敗，拒絕建置。")
    for path in (ffmpeg, license_file, *(ROOT / "scripts" / "remote-c701" / name for name in SCRIPTS)):
        if not path.is_file():
            raise ValueError(f"建置來源缺少必要檔案：{path.name}")
    if output.exists():
        raise ValueError("輸出目錄已存在；請使用新的版本目錄，避免覆蓋舊包。")
    output.mkdir(parents=True)
    shutil.copy2(go2rtc, output / "go2rtc.exe")
    shutil.copy2(ffmpeg, output / "ffmpeg.exe")
    shutil.copy2(license_file, output / "FFmpeg-LICENSE")
    # The relay control bridge uses only Python's standard library. Bundle a
    # minimal, pinned embedded runtime; never ask the remote user to install
    # Python or copy the host's large site-packages directory.
    python_source = ROOT / "runtime" / "python"
    runtime = output / "runtime"
    runtime.mkdir()
    for source in python_source.iterdir():
        if source.is_file():
            shutil.copy2(source, runtime / source.name)
    scripts = output / "scripts"
    scripts.mkdir()
    for name in SCRIPTS:
        shutil.copy2(ROOT / "scripts" / "remote-c701" / name, scripts / name)
    files = sorted(path for path in output.rglob("*") if path.is_file())
    manifest: dict[str, object] = {
        "bundle": "MeowKeeperPlus-C701-relay-pairing",
        "go2rtc_version": "v1.9.14",
        "config_and_xiaomi_session_included": False,
        "files": {str(path.relative_to(output)).replace("\\", "/"): digest(path) for path in files},
    }
    (output / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                target.write(path, path.relative_to(output.parent))
    return {"output": str(output), "archive": str(archive), "archive_sha256": digest(archive), "file_count": len(files) + 1}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
