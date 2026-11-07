"""Signed file deltas. Unchanged payloads are reused locally, never downloaded."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "kinostudio/MeowKeeperPlus"
MAX_DELTA = 512 * 1024**2


def wide(path: Path) -> Path:
    if os.name != "nt":
        return path
    value = os.path.abspath(path)
    if value.startswith("\\\\?\\"):
        return Path(value)
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def digest(path: Path) -> str:
    path = wide(path)
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError("差量檔案路徑無效。")
    path = PurePosixPath(value)
    if (path.is_absolute() or "\\" in value or path.as_posix() != value
            or any(ord(char) < 32 or char in '<>"|?*' for char in value)
            or any(p in {".", "..", ""} or ":" in p or p.endswith((".", " "))
                   or p.split(".")[0].upper() in {"CON", "NUL", "PRN", "AUX", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}
                   for p in path.parts)
            or any(p.lower().startswith(".env") or p.lower() == "secrets" for p in path.parts)
            or path.suffix.lower() in {".dpapi", ".db", ".sqlite"}):
        raise ValueError("差量檔案路徑不符合安全規則。")
    return value


def plain(path: Path) -> None:
    path = wide(path)
    if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("差量更新不接受重新解析點。")


def records(payload: dict) -> dict[str, dict]:
    result = {}
    seen = set()
    for item in payload["files"]:
        path = relative_path(item["path"])
        if path.casefold() in seen or type(item["size_bytes"]) is not int or item["size_bytes"] < 0 or not re.fullmatch(r"[a-fA-F0-9]{64}", item["sha256"]):
            raise ValueError("檔案清單含有無效或重複記錄。")
        seen.add(path.casefold())
        result[path] = {"size_bytes": item["size_bytes"], "sha256": item["sha256"].lower()}
    if not result or len(result) > 60000:
        raise ValueError("差量檔案清單大小無效。")
    return result


def validate_manifest(manifest: dict) -> dict:
    if (manifest.get("schema") != 2 or manifest.get("kind") != "file_delta"
            or manifest.get("repository") != REPOSITORY
            or not re.fullmatch(r"r[1-9]\d*", manifest.get("version", ""))
            or not re.fullmatch(r"r[1-9]\d*", manifest.get("base_version", ""))
            or manifest.get("tag") != manifest["version"]
            or int(manifest["version"][1:]) <= int(manifest["base_version"][1:])
            or manifest.get("asset") != f"MeowKeeperPlus-{manifest['base_version']}-to-{manifest['version']}-delta.zip"
            or type(manifest.get("size")) is not int or not 0 < manifest["size"] <= MAX_DELTA
            or any(not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get(key, ""))) for key in
                   ("sha256", "base_manifest_sha256", "target_manifest_sha256"))):
        raise ValueError("差量簽章資訊不符合核准契約。")
    changed = records({"files": manifest.get("changed", [])})
    if sum(item["size_bytes"] for item in changed.values()) > 12 * 1024**3:
        raise ValueError("差量解壓大小超過安全上限。")
    overrides = manifest.get("base_overrides", [])
    if not isinstance(overrides, list) or len(overrides) > 16:
        raise ValueError("差量基準修正清單無效。")
    if overrides and any(not name.startswith("src/") for name in records({"files": overrides})):
        raise ValueError("基準修正只接受受簽章核准的第一方原始碼。")
    if len(json.dumps(manifest).encode()) > 60000:
        raise ValueError("差量簽章清單過大，停止發布。")
    if "build_manifest.json" not in changed or changed["build_manifest.json"]["sha256"] != manifest["target_manifest_sha256"]:
        raise ValueError("差量更新缺少受簽章保護的目標清單。")
    deleted = manifest.get("deleted")
    if not isinstance(deleted, list) or len(deleted) > 60000:
        raise ValueError("差量刪除清單無效。")
    names = [relative_path(path).casefold() for path in deleted]
    if len(set(names)) != len(names) or set(names) & {path.casefold() for path in changed}:
        raise ValueError("差量新增與刪除清單衝突。")
    return manifest


def verify_delta(envelope: dict, public_key: bytes) -> dict:
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa

    manifest = envelope["manifest"]
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    eddsa.new(ECC.import_key(public_key), "rfc8032").verify(canonical, base64.b64decode(envelope["signature"], validate=True))
    return validate_manifest(manifest)


def version(payload: dict) -> str:
    match = re.search(r"MeowKeeperPlus-portable-\d{4}-\d{2}-\d{2}-(r\d+)$", str(payload.get("output", "")))
    if payload.get("package_type") != "windows_portable" or not match:
        raise ValueError("無法確認發布版本基準。")
    return match[1]


def verifier(app_root: Path):
    path = app_root / "tools/verify_portable.py"
    plain(path)
    spec = importlib.util.spec_from_file_location("delta_portable_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_portable


def _runtime_generated(file: Path, name: str) -> bool:
    """Recognize only installed runtime output; assemble never copies these files."""
    if name in {"logs/go2rtc.log", "logs/go2rtc.log.1"}:
        return file.is_file()
    font_cache = re.fullmatch(r"data/matplotlib/fontlist-v([0-9]{1,5})\.json", name)
    settings = name in {"data/ultralytics/settings.json", "data/ultralytics/Ultralytics/settings.json"}
    if not font_cache and not settings:
        return False
    if not file.is_file() or file.stat().st_size > (4 * 1024**2 if font_cache else 65536):
        raise ValueError("執行期快取大小無效；已停止更新。")
    payload = json.loads(file.read_text(encoding="utf-8-sig"))
    valid = isinstance(payload, dict)
    if font_cache:
        valid = (valid and type(payload.get("_version")) is int
                 and payload["_version"] == int(font_cache[1])
                 and isinstance(payload.get("afmlist"), list) and isinstance(payload.get("ttflist"), list))
    else:
        valid = (valid and isinstance(payload.get("settings_version"), str)
                 and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", payload["settings_version"]))
                 and all(isinstance(payload.get(key), str) for key in ("datasets_dir", "weights_dir", "runs_dir")))
    if not valid:
        raise ValueError("執行期快取格式無效；已停止更新。")
    return True


def validate_base(app_root: Path, manifest: dict) -> dict:
    """Before any ZIP download, reject other versions and modified installed bytes."""
    app_root = wide(app_root)
    plain(app_root)
    path = app_root / "build_manifest.json"
    plain(path)
    if digest(path) != manifest["base_manifest_sha256"]:
        raise ValueError("差量版本基準不符；已停止，不會下載完整包。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if version(payload) != manifest["base_version"]:
        raise ValueError("目前版本不是這個差量包的基準；不會下載完整包。")
    files = records(payload)
    overrides = records({"files": manifest["base_overrides"]}) if manifest.get("base_overrides") else {}
    if set(overrides) - set(files):
        raise ValueError("基準修正含有原版本不存在的檔案。")
    files.update(overrides)
    checked = set()
    for name, item in files.items():
        file = app_root / name
        for parent in file.parents:
            if parent in checked:
                break
            plain(parent)
            checked.add(parent)
            if parent == app_root:
                break
        plain(file)
        if file.stat().st_size != item["size_bytes"] or digest(file) != item["sha256"]:
            raise ValueError("已安裝程式與差量基準不符；已停止，不會改抓完整包。")
    pending = [app_root]
    while pending:
        for file in pending.pop().iterdir():
            plain(file)
            if file.is_dir():
                pending.append(file)
            elif file != path and file.suffix.lower() != ".pyc" and "__pycache__" not in file.parts:
                name = file.relative_to(app_root).as_posix()
                if name not in files:
                    if _runtime_generated(file, name):
                        continue
                    if not re.fullmatch(r"data/locks/MeowKeeperPlus(?:Host|Tray|Go2Rtc)\.json", name):
                        raise ValueError("已安裝程式含有基準外檔案；不會自動改抓完整包。")
                    if file.stat().st_size > 16384:
                        raise ValueError("執行期鎖檔大小無效。")
                    lock = json.loads(file.read_text(encoding="utf-8"))
                    if (not isinstance(lock, dict) or type(lock.get("pid")) is not int or lock["pid"] < 1
                            or lock.get("lock_name") != file.stem or not isinstance(lock.get("command_line"), list)
                            or not isinstance(lock.get("executable"), str)):
                        raise ValueError("執行期鎖檔無效。")
    return files


def extract_payload(archive: Path, manifest: dict, destination: Path) -> None:
    archive, destination = wide(archive), wide(destination)
    if destination.exists() or archive.stat().st_size != manifest["size"] or digest(archive) != manifest["sha256"]:
        raise ValueError("差量套件雜湊／大小不符，或暫存目錄已存在。")
    expected = records({"files": manifest["changed"]})
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if len(infos) != len(expected) or len({item.filename.casefold() for item in infos}) != len(infos):
            raise ValueError("差量壓縮包包含額外或重複項目。")
        for item in infos:
            name = relative_path(item.filename)
            if name not in expected or stat.S_ISLNK(item.external_attr >> 16) or item.is_dir() or item.file_size != expected[name]["size_bytes"]:
                raise ValueError("差量壓縮包檔案清單不符。")
        destination.mkdir()
        for name, item in expected.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(name) as source, path.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if digest(path) != item["sha256"]:
                raise ValueError("差量檔案雜湊不符。")


def assemble(app_root: Path, archive: Path, manifest: dict, destination: Path, *, on_file=None) -> None:
    app_root, archive, destination = wide(app_root), wide(archive), wide(destination)
    base = validate_base(app_root, manifest)
    if destination.exists():
        raise ValueError("差量重建目錄已存在，拒絕覆寫。")
    payload = destination.with_name(destination.name + "-payload")
    extract_payload(archive, manifest, payload)
    target_raw = (payload / "build_manifest.json").read_bytes()
    target = json.loads(target_raw)
    if version(target) != manifest["version"]:
        raise ValueError("差量目標版本與簽章不符。")
    files = records(target)
    if shutil.disk_usage(destination.parent).free < sum(item["size_bytes"] for item in files.values()) + 512 * 1024**2:
        raise ValueError("本機重建空間不足；不會下載完整包。")
    changed = records({"files": manifest["changed"]})
    actual_changed = {name for name, item in files.items() if base.get(name) != item} | {"build_manifest.json"}
    if actual_changed != set(changed) or set(base) - set(files) != set(manifest["deleted"]):
        raise ValueError("差量內容不是基準到目標的精確差異。")
    if any(files[name] != changed[name] for name in actual_changed - {"build_manifest.json"}):
        raise ValueError("差量目標檔案雜湊契約不符。")
    destination.mkdir()
    for index, (name, item) in enumerate(files.items(), 1):
        source = payload / name if name in changed else app_root / name
        plain(source)
        output = destination / name
        output.parent.mkdir(parents=True, exist_ok=True)
        if name in changed:
            shutil.copy2(source, output)
        else:
            try:
                os.link(source, output)
            except OSError:
                shutil.copy2(source, output)
        if digest(output) != item["sha256"]:
            raise ValueError("本機重用檔案在重建期間已改變；已停止。")
        if on_file:
            on_file(index, len(files))
    (destination / "build_manifest.json").write_bytes(target_raw)
    verifier(app_root)(destination)
