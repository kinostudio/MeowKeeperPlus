"""Pinned official go2rtc release and hash verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import Go2RtcReleaseManifest

OFFICIAL_REPOSITORY = "https://github.com/AlexxIT/go2rtc"
OFFICIAL_RELEASE_TAG = "v1.9.14"
OFFICIAL_ASSET_NAME = "go2rtc_win64.zip"
OFFICIAL_DOWNLOAD_URL = f"{OFFICIAL_REPOSITORY}/releases/download/{OFFICIAL_RELEASE_TAG}/{OFFICIAL_ASSET_NAME}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def manifest_path(root: Path) -> Path:
    return root / "vendor" / "go2rtc" / "model_manifest.json"


def load_manifest(path: Path) -> Go2RtcReleaseManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("go2rtc manifest 必須是 JSON 物件。")
    fields = {field.name for field in Go2RtcReleaseManifest.__dataclass_fields__.values()}
    values = {key: payload[key] for key in fields}
    return Go2RtcReleaseManifest(**values)


def verify_manifest(root: Path, executable: Path | None = None) -> Go2RtcReleaseManifest:
    path = manifest_path(root)
    manifest = load_manifest(path)
    if manifest.repository != OFFICIAL_REPOSITORY:
        raise ValueError("go2rtc manifest 不是官方 AlexxIT/go2rtc repository。")
    if manifest.release_tag != OFFICIAL_RELEASE_TAG or manifest.asset_name != OFFICIAL_ASSET_NAME:
        raise ValueError("go2rtc manifest release 或 asset 未固定於核准版本。")
    if manifest.download_url != OFFICIAL_DOWNLOAD_URL:
        raise ValueError("go2rtc manifest download URL 不是官方固定 URL。")
    if not manifest.zip_sha256 or len(manifest.zip_sha256) != 64 or not manifest.executable_sha256:
        raise ValueError("go2rtc manifest 缺少 SHA-256。")
    target = executable or root / "vendor" / "go2rtc" / manifest.release_tag / "go2rtc.exe"
    if not target.is_file():
        raise FileNotFoundError(f"找不到 go2rtc executable：{target}")
    if target.stat().st_size != manifest.executable_size:
        raise ValueError("go2rtc executable 大小與 manifest 不符。")
    if sha256_file(target) != manifest.executable_sha256.upper():
        raise ValueError("go2rtc executable SHA-256 不符，拒絕執行。")
    return manifest
