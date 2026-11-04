"""只下載並驗證更新；不執行安裝器、不接觸正在使用的資料。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

REPOSITORY = "kinostudio/MeowKeeperPlus"
MANIFEST_URL = f"https://github.com/{REPOSITORY}/releases/latest/download/update-manifest.json"
MAX_ARCHIVE = 6 * 1024**3
MAX_EXPANDED = 12 * 1024**3
HOTFIX_TASK_NAME = "MeowKeeperPlus Update Worker"


def canonical(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_envelope(envelope, public_key):
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa

    manifest = envelope["manifest"]
    eddsa.new(ECC.import_key(public_key), "rfc8032").verify(
        canonical(manifest), base64.b64decode(envelope["signature"], validate=True))
    if manifest.get("schema") == 2:
        from meowkeeper.app.delta_update import validate_manifest
        return validate_manifest(manifest)
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


def validate_hotfix_task_xml(xml_text, *, expected_runtime, app_root, data_root, updater_root):
    """Fail closed unless the registered task is the fixed, versioned SYSTEM worker."""
    try:
        root = ET.fromstring(xml_text)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        user_id = root.findtext("t:Principals/t:Principal/t:UserId", namespaces=ns)
        triggers = root.find("t:Triggers", ns)
        actions = root.findall("t:Actions/t:Exec", ns)
        if user_id != "S-1-5-18" or (triggers is not None and list(triggers)) or len(actions) != 1:
            return False
        settings_enabled = root.findtext("t:Settings/t:Enabled", namespaces=ns)
        if settings_enabled is not None and settings_enabled.strip().lower() == "false":
            return False
        if root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns) != "IgnoreNew":
            return False
        action = actions[0]
        command = action.findtext("t:Command", namespaces=ns) or ""
        arguments = action.findtext("t:Arguments", namespaces=ns) or ""
        def normalize(value):
            return os.path.normcase(os.path.abspath(value))
        if normalize(command) != normalize(str(expected_runtime)):
            return False
        if normalize(action.findtext("t:WorkingDirectory", namespaces=ns) or "") != normalize(str(updater_root)):
            return False
        match = re.fullmatch(
            r'-NoProfile\s+-ExecutionPolicy\s+Bypass\s+-File\s+"([^"\r\n]+)"\s+'
            r'-AppRoot\s+"([^"\r\n]+)"\s+-DataRoot\s+"([^"\r\n]+)"\s*',
            arguments,
            flags=re.IGNORECASE,
        )
        if not match:
            return False
        expected_arguments = (
            f'-NoProfile -ExecutionPolicy Bypass -File "{match.group(1)}" '
            f'-AppRoot "{app_root}" -DataRoot "{data_root}"'
        )
        if arguments.strip().casefold() != expected_arguments.casefold():
            return False
        helper = Path(match.group(1))
        app_path, data_path = match.group(2), match.group(3)
        if (normalize(app_path) != normalize(str(app_root))
                or normalize(data_path) != normalize(str(data_root))
                or normalize(str(helper.parent)) != normalize(str(updater_root))
                or not re.fullmatch(r"Apply-MeowKeeperPlusUpdate-[a-f0-9]{16}\.ps1", helper.name, re.IGNORECASE)
                or helper.is_symlink() or not helper.is_file()):
            return False
        attributes = getattr(helper.stat(), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return False
        digest = hashlib.sha256(helper.read_bytes()).hexdigest()
        return digest[:16].lower() == helper.stem.rsplit("-", 1)[-1].lower()
    except (ET.ParseError, OSError, ValueError, TypeError):
        return False


def _decode_task_xml(output):
    if isinstance(output, str):
        return output
    if output.startswith((b"\xff\xfe", b"\xfe\xff")):
        return output.decode("utf-16")
    if b"\x00" in output[:256]:
        return output.decode("utf-16-le")
    return output.decode("utf-8-sig")


class UpdateManager:
    def __init__(self, app_root, data_root, *, hotfix_task_available=None):
        self.app_root, self.root = Path(app_root), Path(data_root) / "updates"
        self.worker_status_path = self.app_root.parent / "Updater" / "hotfix-status.json"
        self._lock = threading.Lock()
        self._manifest = None
        self._envelope = None
        self._prepared_dir = None
        self._hotfix_task_available_override = hotfix_task_available
        self.state = {"status": "not_checked", "message_zh_tw": "尚未檢查更新；不會自動停止監控。"}

    def snapshot(self):
        worker = self._load_worker_status()
        if self.state.get("status") not in {"checking", "downloading", "applying", "available"}:
            self._discover_prepared()
        if worker is not None:
            state_status = self.state.get("status")
            same_prepared = bool(
                self.state.get("prepared_id")
                and self.state.get("prepared_id") == worker.get("prepared_id")
            )
            same_request = bool(
                self.state.get("request_id")
                and self.state.get("request_id") == worker.get("request_id")
            )
            if worker["status"] == "applying" and (
                (state_status == "applying" and same_request)
                or (state_status == "prepared" and same_prepared)
            ):
                self.state = dict(worker)
            elif worker["status"] == "applied":
                if same_request or (same_prepared and state_status == "prepared"):
                    self.state = dict(worker)
            elif worker["status"] == "failed":
                if same_request or (same_prepared and state_status == "prepared"):
                    if self.state.get("package_path") and self.state.get("prepared_id") == worker.get("prepared_id"):
                        self.state["hotfix_result"] = "failed"
                        self.state["message_zh_tw"] = worker.get(
                            "message_zh_tw", "更新未完成；舊版已恢復，可重新嘗試。"
                        )
                        self.state["status"] = "prepared"
                    else:
                        self.state = dict(worker)
            elif worker["status"] == "rollback_failed" and (same_request or same_prepared):
                self.state = dict(worker)
        state = dict(self.state)
        prepared = state.get("status") == "prepared"
        state["apply_available"] = prepared and self._hotfix_task_available()
        if prepared and not state["apply_available"]:
            state["install_command"] = "& '" + str(Path(state["package_path"]) / "Install-MeowKeeperPlus.cmd").replace("'", "''") + "' -Upgrade"
        return state

    def _hotfix_task_available(self):
        if self._hotfix_task_available_override is not None:
            return bool(self._hotfix_task_available_override)
        if os.name != "nt":
            return False
        program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        expected_app = (program_data / "MeowKeeperPlus" / "App").resolve()
        try:
            if self.app_root.resolve() != expected_app:
                return False
        except OSError:
            return False
        updater_root = program_data / "MeowKeeperPlus" / "Updater"
        data_root = program_data / "MeowKeeperPlus" / "Data"
        scheduler = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe"
        if not updater_root.is_dir() or not scheduler.is_file():
            return False
        try:
            result = subprocess.run(
                [str(scheduler), "/Query", "/XML", "/TN", HOTFIX_TASK_NAME],
                capture_output=True, text=False,
                timeout=8, check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0 or len(result.stdout) > 128 * 1024:
            return False
        expected_runtime = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        return validate_hotfix_task_xml(
            _decode_task_xml(result.stdout), expected_runtime=expected_runtime, app_root=expected_app,
            data_root=data_root, updater_root=updater_root,
        )

    def _discover_prepared(self):
        if self._prepared_dir is not None and self._prepared_dir.is_dir():
            return
        try:
            key_path = self.app_root / "configs" / "update-public.pem"
            key = key_path.read_bytes()
            candidates = sorted(self.root.glob("prepared-*"), key=lambda path: path.stat().st_mtime, reverse=True)
            for candidate in candidates:
                if (not candidate.is_dir() or candidate.is_symlink()
                        or not re.fullmatch(r"prepared-[a-f0-9]{8,32}", candidate.name)):
                    continue
                envelope_path = candidate / "update-manifest.json"
                if not envelope_path.is_file() or not (candidate / "package.zip").is_file() or not (candidate / "app").is_dir():
                    continue
                envelope = json.loads(envelope_path.read_bytes())
                manifest = verify_envelope(envelope, key)
                self._envelope, self._manifest, self._prepared_dir = envelope, manifest, candidate
                self.state = {
                    "status": "prepared", "version": manifest["version"],
                    "package_path": str(candidate / "app"),
                    "prepared_id": candidate.name,
                    "message_zh_tw": "更新包已驗證。按「套用更新」後會短暫停止監控；更新工具會再次驗證套件，失敗時嘗試恢復舊版。",
                }
                return
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            return

    def _load_worker_status(self):
        path = self.worker_status_path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or payload.get("schema") != 1
                    or payload.get("status") not in {"applying", "applied", "failed", "rollback_failed"}
                    or not re.fullmatch(r"[a-f0-9]{32}", str(payload.get("request_id", "")))
                    or not re.fullmatch(r"prepared-[a-f0-9]{8,32}", str(payload.get("prepared_id", "")))
                    or not re.fullmatch(r"r[1-9]\d*", str(payload.get("version", "")))):
                return None
            return payload
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

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
                envelope = json.loads(path.read_bytes())
                manifest = verify_envelope(envelope, key)
            from meowkeeper.app.web.release_info import release_information
            current = release_information(self.app_root)["version"]
            available = not re.fullmatch(r"r\d+", current) or int(manifest["version"][1:]) > int(current[1:])
            if available and manifest.get("kind") != "file_delta":
                self._manifest = self._envelope = None
                self.state = {"status": "failed", "reason": "delta_unavailable",
                              "message_zh_tw": "發布區尚未提供適用的差量包；不會自動下載完整包。監控持續。"}
                return
            self._manifest = manifest if available else None
            self._envelope = envelope if available else None
            if available and manifest.get("kind") == "file_delta":
                from meowkeeper.app.delta_update import validate_base
                validate_base(self.app_root, manifest)
            self.state = {"status": "available" if available else "current", "version": manifest["version"],
                          "message_zh_tw": ("找到已簽章差量更新；只下載變更檔案，不會下載完整包。" if manifest.get("kind") == "file_delta" else "找到新版，簽章驗證成功；下載不會停止監控。") if available else "目前版本無需更新。"}
        except Exception as error:  # noqa: BLE001 - network cannot stop media
            self._manifest = None
            self._envelope = None
            self.state = {"status": "failed", "error_type": type(error).__name__,
                          "message_zh_tw": "更新檢查未完成；可能版本基準／安裝檔案不符、網路不可達或簽章不符。不會自動下載完整包，原監控不受影響。"}
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
            if self._envelope is None:
                raise ValueError("缺少已驗證的官方簽章資料，請重新檢查新版。")
            manifest = dict(self._manifest)
            if manifest.get("kind") == "file_delta":
                from meowkeeper.app.delta_update import validate_base
                validate_base(self.app_root, manifest)
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
            self._prepared_dir = folder
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
            envelope_path = folder / "update-manifest.json"
            envelope_path.write_text(json.dumps(self._envelope, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
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
            if manifest.get("kind") == "file_delta":
                from meowkeeper.app.delta_update import assemble
                self._report_progress(version, "rebuild", "在本機重用未變更檔案（不下載 runtime／模型）",
                                      downloaded=downloaded, total_download=total_download)
                assemble(self.app_root, archive, manifest, package,
                         on_file=lambda done, total: self._report_progress(version, "rebuild", "本機重建差量版本",
                             completed=done, total=total, downloaded=downloaded, total_download=total_download))
            else:
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
                          "message_zh_tw": "更新包已驗證。安裝版按「套用更新」即可；會短暫停止監控，套用前會再次驗證套件。可攜版請依備援安裝說明手動更新。",
                          "package_path": str(package), "prepared_id": folder.name}
            folder = None
        except Exception as error:  # noqa: BLE001
            failed_stage = self.state.get("stage_label_zh_tw", "更新準備")
            self.state = {"status": "failed", "error_type": type(error).__name__,
                          "stage_label_zh_tw": failed_stage,
                          "message_zh_tw": f"{failed_stage}未完成；舊程式與資料未修改。請確認空間、網路與發布狀態。"}
        finally:
            if folder is not None:
                shutil.rmtree(folder)
                self._prepared_dir = None
            self._lock.release()

    def apply(self):
        """Queue only a signed, already-prepared package for the fixed SYSTEM task."""
        if not self._lock.acquire(False):
            raise RuntimeError("更新作業仍在進行，請稍候。")
        request_path = self.root / "apply-request.json"
        try:
            if self.state.get("status") != "prepared" or self._manifest is None or self._prepared_dir is None:
                self._discover_prepared()
            if self.state.get("status") != "prepared" or self._manifest is None or self._prepared_dir is None:
                raise ValueError("請先下載並驗證更新包。")
            if not self._hotfix_task_available():
                raise RuntimeError("此安裝尚未註冊安全更新工作；請使用安裝版完成一次更新註冊，或依備援安裝說明手動更新。")
            if request_path.exists():
                raise RuntimeError("已有更新要求等待處理；請勿重複套用。")
            request_id = uuid.uuid4().hex
            prepared_id = self._prepared_dir.name
            if not re.fullmatch(r"prepared-[a-f0-9]{8,32}", prepared_id):
                raise ValueError("更新暫存識別不正確。")
            request = {
                "schema": 1, "request_id": request_id, "prepared_id": prepared_id,
                "version": self._manifest["version"],
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            self.root.mkdir(parents=True, exist_ok=True)
            with request_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(request, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                scheduler = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe"
                result = subprocess.run(
                    [str(scheduler), "/Run", "/TN", HOTFIX_TASK_NAME],
                    capture_output=True, text=True, timeout=15, check=False, shell=False,
                )
                if result.returncode != 0:
                    raise RuntimeError("Windows 未能啟動已註冊的更新工作。")
            except (OSError, subprocess.TimeoutExpired, RuntimeError):
                try:
                    current = json.loads(request_path.read_text(encoding="utf-8"))
                    if current.get("request_id") == request_id:
                        request_path.unlink()
                except (OSError, ValueError, TypeError):
                    pass
                raise
            self.state = {
                "status": "applying", "version": self._manifest["version"],
                "message_zh_tw": "已啟動本機安全更新工作。監控與 Dashboard 將短暫中斷，請勿關機或重複操作。",
                "request_id": request_id, "prepared_id": prepared_id,
                "package_path": str(self._prepared_dir / "app"),
            }
            return self.snapshot()
        finally:
            self._lock.release()
