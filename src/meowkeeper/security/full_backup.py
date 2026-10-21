"""Password-protected full application-data backup.

The outer file is a normal ZIP so it is easy to move between Windows hosts.  The
actual application data is stored in an authenticated AES-GCM payload inside the
ZIP; the outer manifest intentionally contains no credentials or personal data.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

MIN_PASSWORD_LENGTH = 6
ARCHIVE_FORMAT = "meowkeeper-full-backup"
ARCHIVE_VERSION = 1
PAYLOAD_HEADER = "meowkeeper-full-backup-payload"
CHUNK_SIZE = 1024 * 1024
MAX_BACKUP_BYTES = 8 * 1024 * 1024 * 1024
ProgressCallback = Callable[[dict[str, Any]], None]


class FullBackupError(ValueError):
    """A safe, user-facing full-backup validation or restore error."""


def _key(password: str, salt: bytes) -> bytes:
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > 1024:
        raise FullBackupError(f"備份密碼需至少 {MIN_PASSWORD_LENGTH} 個字元，且不得超過 1024 個字元。")
    import hashlib as _hashlib

    return _hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**15,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )


def _item_category(name: str | None) -> str | None:
    """Return a privacy-safe category; never disclose an archive path or filename."""

    if not name:
        return None
    normalized = name.replace("\\", "/").lower()
    if normalized.startswith("config/"):
        return "一般設定"
    if normalized.startswith("database/"):
        return "事件與回饋資料庫"
    if normalized.startswith("security/"):
        return "安全憑證（加密保存）"
    if normalized.startswith("data/pets/"):
        return "寵物登錄與參考照片"
    if normalized.startswith("data/events/"):
        return "事件影片與截圖"
    if normalized.startswith("data/state/"):
        return "通知與偏好設定"
    if normalized.startswith("data/"):
        return "其他應用資料"
    if normalized == "manifest.json":
        return "備份索引"
    return None


def _report_progress(
    callback: ProgressCallback | None,
    phase: str,
    *,
    completed: int = 0,
    total: int = 0,
    item_name: str | None = None,
    bytes_done: int | None = None,
    bytes_total: int | None = None,
    item_complete: bool = False,
) -> None:
    if callback is None:
        return
    callback(
        {
            "phase": phase,
            "completed": max(0, int(completed)),
            "total": max(0, int(total)),
            "item_label": _item_category(item_name),
            "bytes_done": max(0, int(bytes_done)) if bytes_done is not None else None,
            "bytes_total": max(0, int(bytes_total)) if bytes_total is not None else None,
            "item_complete": bool(item_complete),
        }
    )


def _sha256(
    path: Path,
    *,
    on_progress: ProgressCallback | None = None,
    phase: str = "indexing",
    completed: int = 0,
    total: int = 0,
    item_name: str | None = None,
) -> str:
    digest = hashlib.sha256()
    bytes_done = 0
    bytes_total = path.stat().st_size
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
            bytes_done += len(chunk)
            _report_progress(
                on_progress, phase, completed=completed, total=total, item_name=item_name,
                bytes_done=bytes_done, bytes_total=bytes_total,
            )
    _report_progress(
        on_progress, phase, completed=completed, total=total, item_name=item_name,
        bytes_done=bytes_total, bytes_total=bytes_total, item_complete=True,
    )
    return digest.hexdigest()


def _copy_stream(
    source: Any,
    destination: Any,
    *,
    on_progress: ProgressCallback | None,
    phase: str,
    completed: int = 0,
    total: int = 0,
    item_name: str | None = None,
    bytes_total: int | None = None,
) -> int:
    bytes_done = 0
    while chunk := source.read(CHUNK_SIZE):
        destination.write(chunk)
        bytes_done += len(chunk)
        _report_progress(
            on_progress, phase, completed=completed, total=total, item_name=item_name,
            bytes_done=bytes_done, bytes_total=bytes_total,
        )
    _report_progress(
        on_progress, phase, completed=completed, total=total, item_name=item_name,
        bytes_done=bytes_done, bytes_total=bytes_total, item_complete=True,
    )
    return bytes_done


def _safe_archive_name(name: str) -> str:
    candidate = PurePosixPath(name)
    if (
        not name
        or candidate.is_absolute()
        or ".." in candidate.parts
        or (candidate.parts and ":" in candidate.parts[0])
    ):
        raise FullBackupError("備份包含不安全的檔案路徑。")
    normalized = candidate.as_posix()
    if normalized.startswith("/") or "\\" in normalized:
        raise FullBackupError("備份包含不安全的檔案路徑。")
    return normalized


def _copy_sqlite_snapshot(
    database_path: Path,
    destination: Path,
    *,
    connection: sqlite3.Connection | None = None,
    connection_lock: Any | None = None,
) -> bool:
    """Create a consistent SQLite snapshot, including WAL state."""

    if connection is not None:
        lock = connection_lock
        if lock is None:
            class _NullLock:
                def __enter__(self) -> Any:
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

            lock = _NullLock()
        with lock:
            connection.commit()
            target = sqlite3.connect(str(destination))
            try:
                connection.backup(target)
                target.commit()
            finally:
                target.close()
        return True

    if not database_path.is_file():
        return False
    source = sqlite3.connect(str(database_path), timeout=10)
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    return True


def _iter_data_files(data_root: Path, database_path: Path) -> list[tuple[str, Path]]:
    if not data_root.is_dir():
        return []
    database_resolved = database_path.resolve()
    entries: list[tuple[str, Path]] = []
    for path in sorted(data_root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(data_root).as_posix()
        if path.resolve() == database_resolved or path.name in {
            f"{database_path.name}-wal",
            f"{database_path.name}-shm",
        }:
            continue
        if relative.startswith(("cache/ring/", "cache/preview/", "secrets/vision/")):
            continue
        if path.name.endswith((".tmp", ".lock", ".pid", ".shm", ".wal")):
            continue
        entries.append((f"data/{relative}", path))
    return entries


def _write_encrypted_payload(
    source: Path,
    destination: Path,
    password: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> None:
    from Crypto.Cipher import AES

    salt, nonce = os.urandom(16), os.urandom(12)
    cipher = AES.new(_key(password, salt), AES.MODE_GCM, nonce=nonce)
    header = {
        "format": PAYLOAD_HEADER,
        "version": ARCHIVE_VERSION,
        "kdf": "scrypt-n32768-r8-p1",
        "cipher": "aes-256-gcm",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
    }
    with source.open("rb") as source_handle, destination.open("wb") as target:
        target.write(json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n")
        bytes_done = 0
        bytes_total = source.stat().st_size
        while chunk := source_handle.read(CHUNK_SIZE):
            target.write(cipher.encrypt(chunk))
            bytes_done += len(chunk)
            _report_progress(
                on_progress, "encrypting", bytes_done=bytes_done, bytes_total=bytes_total,
            )
        target.write(cipher.digest())
        _report_progress(
            on_progress, "encrypting", completed=1, total=1,
            bytes_done=bytes_total, bytes_total=bytes_total, item_complete=True,
        )


def _decrypt_encrypted_payload(
    source: Path,
    destination: Path,
    password: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> None:
    from Crypto.Cipher import AES

    with source.open("rb") as source_handle:
        header_line = source_handle.readline()
        if not header_line or len(header_line) > 4096:
            raise FullBackupError("完整備份格式無法辨識。")
        try:
            header = json.loads(header_line.decode("utf-8"))
            salt = base64.b64decode(header["salt"], validate=True)
            nonce = base64.b64decode(header["nonce"], validate=True)
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise FullBackupError("完整備份格式無法辨識。") from exc
        if (header.get("format"), header.get("version"), header.get("kdf"), header.get("cipher")) != (
            PAYLOAD_HEADER,
            ARCHIVE_VERSION,
            "scrypt-n32768-r8-p1",
            "aes-256-gcm",
        ) or len(salt) != 16 or len(nonce) != 12:
            raise FullBackupError("完整備份格式或版本不支援。")
        source_handle.seek(0, os.SEEK_END)
        end = source_handle.tell()
        payload_start = len(header_line)
        if end < payload_start + 16:
            raise FullBackupError("完整備份內容不完整。")
        source_handle.seek(end - 16)
        tag = source_handle.read(16)
        source_handle.seek(payload_start)
        remaining = end - payload_start - 16
        bytes_total = remaining
        bytes_done = 0
        cipher = AES.new(_key(password, salt), AES.MODE_GCM, nonce=nonce)
        with destination.open("wb") as target:
            while remaining:
                chunk = source_handle.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise FullBackupError("完整備份內容不完整。")
                target.write(cipher.decrypt(chunk))
                remaining -= len(chunk)
                bytes_done += len(chunk)
                _report_progress(
                    on_progress, "decrypting", bytes_done=bytes_done, bytes_total=bytes_total,
                )
            try:
                cipher.verify(tag)
            except ValueError as exc:
                target.close()
                destination.unlink(missing_ok=True)
                raise FullBackupError("備份密碼錯誤、檔案遭修改或格式損毀；未匯入任何資料。") from exc
        _report_progress(
            on_progress, "decrypting", completed=1, total=1,
            bytes_done=bytes_total, bytes_total=bytes_total, item_complete=True,
        )


def _manifest_for(
    entries: list[tuple[str, Path]],
    *,
    includes_secrets: bool,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    files = []
    for index, (name, path) in enumerate(entries, start=1):
        files.append(
            {
                "path": name,
                "size": path.stat().st_size,
                "sha256": _sha256(
                    path,
                    on_progress=on_progress,
                    phase="indexing",
                    completed=index,
                    total=len(entries),
                    item_name=name,
                ),
            }
        )
    return {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "includes_secrets": includes_secrets,
        "excluded": ["data/cache/ring", "data/cache/preview", "data/secrets/vision（換電腦後重新設定 API Key）", "runtime、模型與 vendor 檔案"],
        "files": files,
    }


def create_full_backup(
    *,
    config_path: str | Path,
    data_root: str | Path,
    database_path: str | Path,
    password: str,
    telegram: Mapping[str, Any] | None = None,
    database_connection: sqlite3.Connection | None = None,
    database_lock: Any | None = None,
    output_directory: str | Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Create an encrypted ZIP containing application data and a DB snapshot."""

    _key(password, os.urandom(16))
    config = Path(config_path).resolve()
    data = Path(data_root).resolve()
    database = Path(database_path).resolve()
    output_root = Path(output_directory or tempfile.mkdtemp(prefix="meowkeeper-full-backup-")).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    working = Path(tempfile.mkdtemp(prefix="payload-", dir=output_root))
    try:
        _report_progress(on_progress, "snapshot", completed=0, total=1)
        db_snapshot = working / "meowkeeper.db"
        entries: list[tuple[str, Path]] = []
        if config.is_file():
            entries.append(("config/config.json", config))
        if _copy_sqlite_snapshot(database, db_snapshot, connection=database_connection, connection_lock=database_lock):
            entries.append(("database/meowkeeper.db", db_snapshot))
        entries.extend(_iter_data_files(data, database))
        _report_progress(
            on_progress, "snapshot", completed=1, total=1,
            item_name="database/meowkeeper.db" if db_snapshot.is_file() else None,
            item_complete=db_snapshot.is_file(),
        )

        if telegram is not None:
            telegram_path = working / "telegram.json"
            telegram_path.write_text(json.dumps(dict(telegram), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            entries.append(("security/telegram.json", telegram_path))

        _report_progress(on_progress, "inventory", completed=len(entries), total=len(entries))
        manifest = _manifest_for(
            entries, includes_secrets=telegram is not None, on_progress=on_progress,
        )
        inner_zip = working / "payload.zip"
        with zipfile.ZipFile(inner_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for index, (name, path) in enumerate(entries, start=1):
                info = zipfile.ZipInfo(_safe_archive_name(name))
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as source, archive.open(info, "w") as destination:
                    _copy_stream(
                        source, destination, on_progress=on_progress, phase="packing",
                        completed=index, total=len(entries), item_name=name,
                        bytes_total=path.stat().st_size,
                    )
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))

        encrypted_payload = working / "payload.bin"
        _write_encrypted_payload(inner_zip, encrypted_payload, password, on_progress=on_progress)
        output = output_root / f"MeowKeeperPlus-full-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as outer:
            outer.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            with encrypted_payload.open("rb") as source, outer.open("payload.bin", "w") as destination:
                _copy_stream(
                    source, destination, on_progress=on_progress, phase="writing_archive",
                    completed=1, total=1, item_name="payload.bin",
                    bytes_total=encrypted_payload.stat().st_size,
                )
        if output.stat().st_size > MAX_BACKUP_BYTES:
            output.unlink(missing_ok=True)
            raise FullBackupError("完整備份超過允許大小。請先依保存期限清理舊事件影片。")
        _report_progress(on_progress, "completed", completed=len(entries), total=len(entries))
        return output, {"file_count": len(entries), "archive_bytes": output.stat().st_size, "manifest": manifest}
    finally:
        shutil.rmtree(working, ignore_errors=True)


def _validate_manifest(manifest: Mapping[str, Any], names: set[str]) -> list[dict[str, Any]]:
    if manifest.get("format") != ARCHIVE_FORMAT or manifest.get("version") != ARCHIVE_VERSION:
        raise FullBackupError("完整備份格式或版本不支援。")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise FullBackupError("完整備份缺少檔案清單。")
    normalized: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, Mapping):
            raise FullBackupError("完整備份檔案清單格式不正確。")
        name = _safe_archive_name(str(item.get("path") or ""))
        if name not in names or not isinstance(item.get("size"), int) or not isinstance(item.get("sha256"), str):
            raise FullBackupError("完整備份檔案清單與內容不一致。")
        normalized.append({"path": name, "size": item["size"], "sha256": item["sha256"]})
    return normalized


def restore_full_backup(
    *,
    backup_path: str | Path,
    password: str,
    config_path: str | Path,
    data_root: str | Path,
    database_path: str | Path,
    telegram_store: Any | None = None,
    database_connection: sqlite3.Connection | None = None,
    database_lock: Any | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Verify and restore a full backup without trusting archive paths."""

    _key(password, os.urandom(16))
    backup = Path(backup_path).resolve()
    if not backup.is_file() or backup.stat().st_size > MAX_BACKUP_BYTES:
        raise FullBackupError("找不到完整備份，或備份超過允許大小。")
    staging = Path(tempfile.mkdtemp(prefix="meowkeeper-restore-"))
    try:
        with zipfile.ZipFile(backup, "r") as outer:
            if set(outer.namelist()) != {"manifest.json", "payload.bin"}:
                raise FullBackupError("完整備份外層檔案不完整。")
            try:
                outer_manifest = json.loads(outer.read("manifest.json").decode("utf-8"))
            except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
                raise FullBackupError("完整備份索引無法讀取。") from exc
            payload = staging / "payload.bin"
            payload_total = outer.getinfo("payload.bin").file_size
            with payload.open("wb") as handle, outer.open("payload.bin") as source:
                _copy_stream(
                    source, handle, on_progress=on_progress, phase="reading_archive",
                    completed=1, total=1, item_name="payload.bin", bytes_total=payload_total,
                )
        inner_zip = staging / "payload.zip"
        _decrypt_encrypted_payload(payload, inner_zip, password, on_progress=on_progress)
        extract_root = staging / "files"
        with zipfile.ZipFile(inner_zip, "r") as inner:
            names = {_safe_archive_name(name) for name in inner.namelist()}
            if "manifest.json" not in names:
                raise FullBackupError("完整備份缺少內層索引。")
            inner_manifest = json.loads(inner.read("manifest.json").decode("utf-8"))
            if outer_manifest != inner_manifest:
                raise FullBackupError("完整備份外層與內層索引不一致。")
            file_entries = _validate_manifest(inner_manifest, names)
            content_entries = [item for item in file_entries if item["path"] != "manifest.json"]
            for index, item in enumerate(content_entries, start=1):
                name = item["path"]
                target = extract_root / Path(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with inner.open(name) as source, target.open("wb") as destination:
                    _copy_stream(
                        source, destination, on_progress=on_progress, phase="extracting",
                        completed=index, total=len(content_entries), item_name=name,
                        bytes_total=item["size"],
                    )
            for index, item in enumerate(file_entries, start=1):
                source = extract_root / Path(item["path"])
                digest = _sha256(
                    source,
                    on_progress=on_progress,
                    phase="verifying",
                    completed=index,
                    total=len(file_entries),
                    item_name=item["path"],
                )
                if source.stat().st_size != item["size"] or digest != item["sha256"]:
                    raise FullBackupError("完整備份校驗失敗；未匯入任何資料。")

        config_source = extract_root / "config" / "config.json"
        if config_source.is_file():
            try:
                config_values = json.loads(config_source.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise FullBackupError("備份中的一般設定格式錯誤；尚未套用任何資料。") from exc
            if not isinstance(config_values, Mapping):
                raise FullBackupError("備份中的一般設定格式不正確；尚未套用任何資料。")

        telegram_source = extract_root / "security" / "telegram.json"
        telegram_values: Mapping[str, Any] | None = None
        if telegram_source.is_file():
            try:
                parsed_telegram = json.loads(telegram_source.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise FullBackupError("備份中的 Telegram 憑證格式錯誤；尚未套用任何資料。") from exc
            if not isinstance(parsed_telegram, Mapping):
                raise FullBackupError("備份中的 Telegram 憑證格式不正確；尚未套用任何資料。")
            token_value = parsed_telegram.get("bot_token", "")
            chat_ids_value = parsed_telegram.get("chat_ids", "")
            recipients_value = parsed_telegram.get("recipients")
            if (not isinstance(token_value, str) or not isinstance(chat_ids_value, str)
                    or (recipients_value is not None and not isinstance(recipients_value, list))):
                raise FullBackupError("備份中的 Telegram 通知對象格式不正確；尚未套用任何資料。")
            if not token_value and (chat_ids_value or recipients_value):
                raise FullBackupError("備份缺少 Telegram Bot Token；尚未套用任何資料。")
            telegram_values = parsed_telegram

        root = Path(data_root).resolve()
        _report_progress(on_progress, "restoring", completed=0, total=max(1, len(file_entries)))
        if config_source.is_file():
            destination = Path(config_path).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".restore.tmp")
            shutil.copy2(config_source, temporary)
            os.replace(temporary, destination)
            _report_progress(
                on_progress, "restoring", completed=1, total=max(1, len(file_entries)),
                item_name="config/config.json", item_complete=True,
            )

        db_source = extract_root / "database" / "meowkeeper.db"
        if db_source.is_file():
            if database_connection is not None:
                lock = database_lock
                if lock is None:
                    class _NullLock:
                        def __enter__(self) -> Any:
                            return self

                        def __exit__(self, *_args: object) -> None:
                            return None

                    lock = _NullLock()
                with lock:
                    database_connection.commit()
                    source_connection = sqlite3.connect(str(db_source))
                    try:
                        source_connection.backup(database_connection)
                        database_connection.commit()
                    finally:
                        source_connection.close()
            else:
                destination = Path(database_path).resolve()
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(db_source, destination)
            _report_progress(
                on_progress, "restoring", completed=2 if config_source.is_file() else 1,
                total=max(1, len(file_entries)), item_name="database/meowkeeper.db", item_complete=True,
            )

        restored_files = 0
        data_root_source = extract_root / "data"
        if data_root_source.is_dir():
            root.mkdir(parents=True, exist_ok=True)
            data_files = sorted(source for source in data_root_source.rglob("*") if source.is_file())
            restore_offset = int(config_source.is_file()) + int(db_source.is_file())
            for index, source in enumerate(data_files, start=1):
                relative = source.relative_to(data_root_source)
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                restored_files += 1
                _report_progress(
                    on_progress, "restoring", completed=restore_offset + index,
                    total=max(1, len(file_entries)),
                    item_name=f"data/{relative.as_posix()}", item_complete=True,
                )

        if telegram_source.is_file() and telegram_store is not None:
            values = telegram_values or {}
            telegram_store.clear_telegram()
            token = str(values.get("bot_token") or "")
            if token:
                telegram_store.save_telegram(
                    token,
                    values.get("chat_ids", ""),
                    values.get("recipients"),
                )
            _report_progress(
                on_progress, "restoring", completed=len(file_entries), total=len(file_entries),
                item_name="security/telegram.json", item_complete=True,
            )
        _report_progress(on_progress, "completed", completed=len(file_entries), total=len(file_entries))
        return {"message_zh_tw": "完整資料備份已驗證並還原；請重新啟動 Host。", "restored_files": restored_files}
    except zipfile.BadZipFile as exc:
        raise FullBackupError("ZIP 檔案不完整或結構損毀；尚未套用備份資料。") from exc
    except json.JSONDecodeError as exc:
        raise FullBackupError("備份索引格式錯誤或內容不完整；尚未套用備份資料。") from exc
    except sqlite3.Error as exc:
        raise FullBackupError("SQLite 資料庫讀寫失敗；請確認資料庫可用及磁碟空間。") from exc
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5:
            reason = "檔案存取被 Windows 拒絕；請確認 MeowKeeper+ 資料夾權限及檔案未被占用。"
        elif getattr(exc, "errno", None) == 28 or getattr(exc, "winerror", None) == 112:
            reason = "磁碟空間不足；請先釋出空間後重試。"
        elif isinstance(exc, FileNotFoundError):
            reason = "備份檔或本機資料在處理期間消失；請確認檔案仍存在後重試。"
        else:
            reason = "本機儲存裝置讀寫失敗；請確認磁碟、資料夾權限與可用空間。"
        raise FullBackupError(reason) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


__all__ = ["MIN_PASSWORD_LENGTH", "FullBackupError", "create_full_backup", "restore_full_backup"]
