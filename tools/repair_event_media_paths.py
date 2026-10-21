"""Repair relocated event references only after matching the recorded SHA256.

Read-only by default. Explicit apply creates a live SQLite backup before a
conditional transaction; neither media files nor unrelated rows are changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def repair(data_root: Path, *, apply: bool = False) -> dict:
    if data_root.is_symlink():
        raise ValueError("資料根目錄不可為連結。")
    root = data_root.resolve(strict=True)
    database = root / "meowkeeper.db"
    events = root / "events"
    if database.is_symlink() or events.is_symlink() or not events.is_dir():
        raise ValueError("資料庫或事件目錄不是可安全修復的一般路徑。")
    connection = sqlite3.connect(database.as_uri() + "?mode=" + ("rw" if apply else "ro"), uri=True, timeout=10)
    try:
        rows = connection.execute(
            "SELECT event_id, clip_path, clip_sha256, clip_size_bytes FROM media_events"
        ).fetchall()
        planned = []
        counts = {"already_local": 0, "missing": 0, "unverified": 0, "mismatch": 0}
        for event_id, old_path, expected_hash, expected_size in rows:
            if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", event_id):
                counts["unverified"] += 1
                continue
            target = events / f"event_{event_id}.mp4"
            if target.is_symlink() or not target.is_file() or target.resolve().parent != events:
                counts["missing"] += 1
                continue
            if old_path and Path(old_path).resolve() == target:
                counts["already_local"] += 1
                continue
            if not isinstance(expected_hash, str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", expected_hash):
                counts["unverified"] += 1
                continue
            if expected_size is not None and target.stat().st_size != expected_size:
                counts["mismatch"] += 1
                continue
            with target.open("rb") as media:
                digest = hashlib.sha256()
                for chunk in iter(lambda: media.read(1024 * 1024), b""):
                    digest.update(chunk)
                actual_hash = digest.hexdigest()
            if actual_hash != expected_hash.lower():
                counts["mismatch"] += 1
                continue
            planned.append((str(target), event_id, old_path))
        result = {"mode": "apply" if apply else "read_only", "data_root": str(root),
                  "events": len(rows), "verified_candidates": len(planned), **counts,
                  "updated": 0, "backup": None}
        if apply and planned:
            backup_dir = root / "repair-backups"
            if backup_dir.is_symlink():
                raise ValueError("備份目錄不可為連結。")
            backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = backup_dir / f"event-paths-{stamp}-{uuid4().hex[:8]}.db"
            with closing(sqlite3.connect(backup)) as destination:
                connection.backup(destination)
            result["backup"] = str(backup)
            with connection:
                for new_path, event_id, old_path in planned:
                    cursor = connection.execute(
                        "UPDATE media_events SET clip_path=? WHERE event_id=? AND clip_path IS ?",
                        (new_path, event_id, old_path),
                    )
                    result["updated"] += cursor.rowcount
            result["status"] = "passed"
        else:
            result["status"] = "no_changes" if apply else "diagnosed"
        return result
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="驗證並修復搬移後的事件影片路徑；預設唯讀。")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(args.data_root, apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
