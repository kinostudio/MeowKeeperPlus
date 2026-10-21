"""唯讀輸出行為回報評估，不載入模型、不遷移資料庫、不輸出個人識別。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meowkeeper.core.identity.feedback_learning import behavior_feedback_report


def main():
    parser = argparse.ArgumentParser(description="唯讀檢查逐軌跡行為回饋，不代表辨識準確率。")
    parser.add_argument("--database", type=Path, required=True, help="現有 SQLite 資料庫路徑")
    args = parser.parse_args()
    try:
        connection = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            report = behavior_feedback_report(SimpleNamespace(connection=connection, _connection_lock=threading.RLock()))
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        print("無法讀取回饋評估；請確認資料庫存在且版本支援逐軌跡回報。", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
