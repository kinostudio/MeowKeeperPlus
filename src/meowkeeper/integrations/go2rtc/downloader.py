"""Repository-local downloader for the pinned official Windows release."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .manifest import (
    OFFICIAL_ASSET_NAME,
    OFFICIAL_DOWNLOAD_URL,
    OFFICIAL_RELEASE_TAG,
    OFFICIAL_REPOSITORY,
    sha256_file,
    verify_manifest,
)
from .models import Go2RtcReleaseManifest


class OfficialGo2RtcDownloader:
    """Download and verify only AlexxIT/go2rtc's pinned formal release."""

    def __init__(self, root: str | Path, *, opener: Callable[..., object] | None = None) -> None:
        self.root = Path(root).resolve()
        self.base = self.root / "vendor" / "go2rtc"
        self._opener = opener or urllib.request.urlopen

    @property
    def executable(self) -> Path:
        return self.base / OFFICIAL_RELEASE_TAG / "go2rtc.exe"

    @property
    def manifest(self) -> Path:
        return self.base / "model_manifest.json"

    def ensure_installed(self, *, force: bool = False, timeout_seconds: float = 120.0) -> Go2RtcReleaseManifest:
        if not force and self.manifest.is_file() and self.executable.is_file():
            try:
                return verify_manifest(self.root, self.executable)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        self.base.mkdir(parents=True, exist_ok=True)
        try:
            with self._opener(OFFICIAL_DOWNLOAD_URL, timeout=timeout_seconds) as response:  # type: ignore[arg-type]
                payload = response.read()
        except (OSError, ValueError) as exc:
            raise RuntimeError("無法從官方 go2rtc release 下載 Windows asset。") from exc
        if not payload:
            raise RuntimeError("官方 go2rtc release 回傳空檔案。")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-go2rtc-") as directory:
            zip_path = Path(directory) / OFFICIAL_ASSET_NAME
            zip_path.write_bytes(payload)
            zip_hash = sha256_file(zip_path)
            executable = self._extract_executable(payload, Path(directory) / "extract")
            target_dir = self.base / OFFICIAL_RELEASE_TAG
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "go2rtc.exe"
            shutil.copy2(executable, target)
            manifest = Go2RtcReleaseManifest(
                product="go2rtc",
                repository=OFFICIAL_REPOSITORY,
                release_tag=OFFICIAL_RELEASE_TAG,
                asset_name=OFFICIAL_ASSET_NAME,
                download_url=OFFICIAL_DOWNLOAD_URL,
                downloaded_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                zip_size=len(payload),
                executable_size=target.stat().st_size,
                zip_sha256=zip_hash,
                executable_sha256=sha256_file(target),
                license="MIT",
                minimum_windows="Windows 10/11 64-bit",
                verified_by="MeowKeeper+ official GitHub release bootstrap",
            )
            self.manifest.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return verify_manifest(self.root, self.executable)

    @staticmethod
    def _extract_executable(payload: bytes, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = [member for member in archive.infolist() if Path(member.filename).name.lower() == "go2rtc.exe"]
                if len(members) != 1:
                    raise RuntimeError("官方 zip 未包含唯一 go2rtc.exe。")
                member = members[0]
                target = (destination / Path(member.filename).name).resolve()
                target.relative_to(destination.resolve())
                with archive.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                return target
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            raise RuntimeError("官方 go2rtc zip 無法安全解壓縮。") from exc
