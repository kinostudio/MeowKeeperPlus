"""只下載並驗證更新；不執行安裝器、不接觸正在使用的資料。"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

REPOSITORY = "kinostudio/MeowKeeperPlus"
MANIFEST_URL = f"https://github.com/{REPOSITORY}/releases/latest/download/update-manifest.json"
MAX_ARCHIVE = 6 * 1024**3
MAX_EXPANDED = 12 * 1024**3


def canonical(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_envelope(envelope, public_key):
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa

    manifest = envelope["manifest"]
    eddsa.new(ECC.import_key(public_key), "rfc8032").verify(
        canonical(manifest), base64.b64decode(envelope["signature"], validate=True))
    if (manifest.get("schema") != 1 or manifest.get("repository") != REPOSITORY
            or not re.fullmatch(r"r[1-9]\d*", manifest.get("version", ""))
            or not re.fullmatch(r"r[1-9]\d*", manifest.get("tag", ""))
            or manifest["tag"] != manifest["version"]
            or not re.fullmatch(r"MeowKeeperPlus-[A-Za-z0-9._-]+\.zip", manifest.get("asset", ""))
            or not re.fullmatch(r"[a-fA-F0-9]{64}", manifest.get("sha256", ""))
            or type(manifest.get("size")) is not int or not 0 < manifest["size"] <= MAX_ARCHIVE):
        raise ValueError("更新資訊格式或簽章不符合核准契約。")
    parts = manifest.get("parts", [])
    if parts:
        if not isinstance(parts, list) or not 1 <= len(parts) <= 8:
            raise ValueError("更新分卷格式不正確。")
        for index, part in enumerate(parts, 1):
            if (part.get("name") != manifest["asset"] + f".part{index:02d}"
                    or type(part.get("size")) is not int or not 0 < part["size"] <= 1024**3
                    or not re.fullmatch(r"[a-fA-F0-9]{64}", part.get("sha256", ""))):
                raise ValueError("更新分卷格式不正確。")
        if sum(p["size"] for p in parts) != manifest["size"]:
            raise ValueError("更新分卷大小不符合完整包。")
    elif manifest["size"] > 2 * 1024**3:
        raise ValueError("大型更新包必須使用分卷。")
    return manifest


def safe_extract(archive, target, on_progress=None):
    """拒絕 traversal、symlink、Windows alias 與過大壓縮包。"""
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if len(infos) > 60000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError("更新包超過容量上限。")
        seen = set()
        for item in infos:
            path = PurePosixPath(item.filename)
            key = item.filename.rstrip("/").casefold()
            if (not path.parts or path.is_absolute() or "\\" in item.orig_filename
                    or any(p in {".", ".."} or ":" in p or p.endswith((".", " "))
                           or p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}
                           for p in path.parts)
                    or stat.S_ISLNK(item.external_attr >> 16) or key in seen):
                raise ValueError("更新包包含不安全檔案路徑。")
            seen.add(key)
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        total_bytes = sum(item.file_size for item in infos if not item.is_dir())
        completed_bytes = 0
        for item in infos:
            destination = target.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item, "r") as source, destination.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    completed_bytes += len(chunk)
                    if on_progress is not None:
                        on_progress(completed_bytes, total_bytes)


class UpdateManager:
    def __init__(self, app_root, data_root):
        self.app_root, self.root = Path(app_root), Path(data_root) / "updates"
        self._lock = threading.Lock()
        self._manifest = None
        self.state = {"status": "not_checked", "message_zh_tw": "尚未檢查更新；不會自動停止監控。"}

    def snapshot(self):
        return dict(self.state)

    def _download(self, url, destination, limit, on_chunk=None):
        import logging

        import httpx

        # GitHub asset redirects carry temporary query credentials; never emit HTTP INFO URLs.
        for name in ("httpx", "httpcore"):
            logger = logging.getLogger(name)
            if logger.getEffectiveLevel() < logging.WARNING:
                logger.setLevel(logging.WARNING)

        with httpx.Client(timeout=httpx.Timeout(30, read=60), follow_redirects=True,
                          trust_env=False) as client, client.stream("GET", url) as response:
            response.raise_for_status()
            parsed = urlsplit(str(response.url))
            if parsed.scheme != "https" or not (parsed.hostname == "github.com"
                    or parsed.hostname == "release-assets.githubusercontent.com"
                    or parsed.hostname == "objects.githubusercontent.com"):
                raise ValueError("更新下載來源不在核准範圍。")
            count = 0
            with destination.open("xb") as output:
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > limit:
                        raise ValueError("更新檔案超過容量上限。")
                    output.write(chunk)
                    if on_chunk is not None:
                        on_chunk(len(chunk))

    def _report_progress(self, version, stage, stage_label, *, completed=None, total=None,
                         downloaded=0, total_download=0, part_index=None, part_count=None):
        percent = None
        if total is not None and total > 0:
            percent = round(min(100.0, completed * 100.0 / total), 1)
        self.state = {
            "status": "downloading",
            "version": version,
            "stage": stage,
            "stage_label_zh_tw": stage_label,
            "progress_percent": percent,
            "progress_completed_bytes": completed,
            "progress_total_bytes": total,
            "downloaded_bytes": downloaded,
            "total_bytes": total_download,
            "part_index": part_index,
            "part_count": part_count,
            "message_zh_tw": "正在準備更新；監控持續。",
        }

    def check(self):
        if not self._lock.acquire(False):
            return
        try:
            self.state = {"status": "checking", "message_zh_tw": "正在檢查官方發布區…"}
            key = (self.app_root / "configs/update-public.pem").read_bytes()
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.root) as folder:
                path = Path(folder) / "manifest.json"
                self._download(MANIFEST_URL, path, 65536)
                manifest = verify_envelope(json.loads(path.read_bytes()), key)
            from meowkeeper.app.web.release_info import release_information
            current = release_information(self.app_root)["version"]
            available = not re.fullmatch(r"r\d+", current) or int(manifest["version"][1:]) > int(current[1:])
            self._manifest = manifest if available else None
            self.state = {"status": "available" if available else "current", "version": manifest["version"],
                          "message_zh_tw": "找到新版，簽章驗證成功；下載不會停止監控。" if available else "目前版本無需更新。"}
        except Exception as error:  # noqa: BLE001 - network cannot stop media
            self._manifest = None
            self.state = {"status": "failed", "error_type": type(error).__name__,
                          "message_zh_tw": "更新檢查未完成；可能尚未發布、網路不可達或簽章不符。原監控不受影響。"}
        finally:
            self._lock.release()

    def prepare(self):
        if not self._lock.acquire(False):
            return
        folder = None
        try:
            if self.state.get("status") == "prepared":
                return
            if self._manifest is None:
                raise ValueError("請先檢查新版。")
            manifest = dict(self._manifest)
            version = manifest["version"]
            total_download = manifest["size"]
            downloaded = 0
            last_report_at = 0.0
            last_reported_bytes = 0
            parts = manifest.get("parts") or []

            def report_download_chunk(chunk_size, part_index=None):
                nonlocal downloaded, last_report_at, last_reported_bytes
                downloaded += chunk_size
                now = time.monotonic()
                if downloaded - last_reported_bytes >= 8 * 1024 * 1024 or now - last_report_at >= 0.75:
                    self._report_progress(version, "download", "下載更新",
                                          completed=downloaded, total=total_download,
                                          downloaded=downloaded, total_download=total_download,
                                          part_index=part_index, part_count=len(parts) or None)
                    last_report_at, last_reported_bytes = now, downloaded

            self._report_progress(version, "preflight", "檢查更新空間與下載資訊",
                                  downloaded=0, total_download=total_download)
            self.root.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.root).free < manifest["size"] + MAX_EXPANDED:
                raise ValueError("準備更新需要足夠空間容納新旧版本。")
            folder = Path(tempfile.mkdtemp(prefix="prepared-", dir=self.root))
            archive = folder / "package.zip"
            url = f"https://github.com/{REPOSITORY}/releases/download/{manifest['tag']}/{manifest['asset']}"
            if parts:
                with archive.open("xb") as assembled:
                    for index, part in enumerate(parts):
                        part_number = index + 1
                        label = f"下載更新第 {part_number}/{len(parts)} 卷"
                        self._report_progress(version, "download", label,
                                              completed=downloaded, total=total_download,
                                              downloaded=downloaded, total_download=total_download,
                                              part_index=part_number, part_count=len(parts))
                        piece = folder / f"part-{index}"
                        self._download(url + f".part{part_number:02d}", piece, part["size"],
                                       on_chunk=lambda size, part_number=part_number:
                                       report_download_chunk(size, part_number))
                        part_digest = hashlib.sha256()
                        verified_part = 0
                        last_report_at = 0.0
                        last_reported_bytes = 0
                        with piece.open("rb") as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                part_digest.update(chunk)
                                assembled.write(chunk)
                                verified_part += len(chunk)
                                now = time.monotonic()
                                if (verified_part == part["size"]
                                        or verified_part - last_reported_bytes >= 8 * 1024 * 1024
                                        or now - last_report_at >= 0.75):
                                    self._report_progress(
                                        version, "verify_part", "校驗並合併分卷",
                                        completed=verified_part, total=part["size"],
                                        downloaded=downloaded, total_download=total_download,
                                        part_index=part_number, part_count=len(parts))
                                    last_report_at, last_reported_bytes = now, verified_part
                        if piece.stat().st_size != part["size"] or part_digest.hexdigest() != part["sha256"].lower():
                            raise ValueError("更新分卷校驗失敗。")
                        piece.unlink()
            else:
                self._report_progress(version, "download", "下載更新",
                                      completed=0, total=total_download,
                                      downloaded=0, total_download=total_download)
                self._download(url, archive, manifest["size"],
                               on_chunk=lambda size: report_download_chunk(size))
            digest = hashlib.sha256()
            archive_size = archive.stat().st_size
            verified_archive = 0
            last_report_at = 0.0
            last_reported_bytes = 0
            with archive.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    verified_archive += len(chunk)
                    now = time.monotonic()
                    if (verified_archive == archive_size
                            or verified_archive - last_reported_bytes >= 8 * 1024 * 1024
                            or now - last_report_at >= 0.75):
                        self._report_progress(version, "verify_archive", "校驗完整更新檔",
                                              completed=verified_archive, total=archive_size,
                                              downloaded=downloaded, total_download=total_download)
                        last_report_at, last_reported_bytes = now, verified_archive
            if archive_size != manifest["size"] or digest.hexdigest() != manifest["sha256"].lower():
                raise ValueError("更新檔案校驗失敗。")
            package = folder / "app"
            with zipfile.ZipFile(archive) as bundle:
                extract_total = sum(item.file_size for item in bundle.infolist() if not item.is_dir())
            extracted = 0
            last_report_at = 0.0
            last_reported_bytes = 0

            def report_extract_progress(completed, total):
                nonlocal extracted, last_report_at, last_reported_bytes
                extracted = completed
                now = time.monotonic()
                if (completed == total or completed - last_reported_bytes >= 8 * 1024 * 1024
                        or now - last_report_at >= 0.75):
                    self._report_progress(version, "extract", "解壓更新檔",
                                          completed=completed, total=total,
                                          downloaded=downloaded, total_download=total_download)
                    last_report_at, last_reported_bytes = now, completed

            self._report_progress(version, "extract", "準備解壓更新檔",
                                  completed=0, total=extract_total,
                                  downloaded=downloaded, total_download=total_download)
            safe_extract(archive, package, on_progress=report_extract_progress)
            # Reuse strict release verification; never relax data boundaries.
            import importlib.util
            self._report_progress(version, "verify_package", "驗證程式檔案清單與完整性",
                                  downloaded=downloaded, total_download=total_download)
            spec = importlib.util.spec_from_file_location("release_verifier", self.app_root / "tools/verify_portable.py")
            verifier = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(verifier)
            verifier.verify_portable(package)
            self.state = {"status": "prepared", "version": manifest["version"],
                          "message_zh_tw": "更新包已驗證。請在此電腦執行顯示的安裝命令；不會自動要求 UAC。可攜版可先完整關閉舊版，再開啟新包並使用完整資料備份還原。",
                          "package_path": str(package),
                          "install_command": "& '" + str(package / "Install-MeowKeeperPlus.cmd").replace("'", "''") + "' -Upgrade"}
            archive.unlink()
            folder = None
        except Exception as error:  # noqa: BLE001
            failed_stage = self.state.get("stage_label_zh_tw", "更新準備")
            self.state = {"status": "failed", "error_type": type(error).__name__,
                          "stage_label_zh_tw": failed_stage,
                          "message_zh_tw": f"{failed_stage}未完成；舊程式與資料未修改。請確認空間、網路與發布狀態。"}
        finally:
            if folder is not None:
                shutil.rmtree(folder)
            self._lock.release()
