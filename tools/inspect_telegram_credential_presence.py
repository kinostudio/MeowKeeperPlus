"""僅回報目前 Windows 帳號的 Telegram 憑證存在狀態，不輸出秘密。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.app_root / "src"))
    from meowkeeper.security.credentials import TelegramCredentialStore

    result = {"windows_user": os.getenv("USERNAME", ""), "status": "not_checked"}
    try:
        status = TelegramCredentialStore().status()
        result.update(status="checked", backend=status.get("backend"),
                      bot_token_configured=bool(status.get("bot_token_configured")),
                      chat_ids_configured=bool(status.get("chat_ids_configured")),
                      recipient_count=int(status.get("recipient_count") or 0))
    except (OSError, ValueError, RuntimeError) as error:
        result.update(status="failed", error_type=type(error).__name__)
    if args.data_root:
        access = {}
        for name in ("config.json", "meowkeeper.db", "locks/MeowKeeperPlusHost.json"):
            path = args.data_root / name
            try:
                descriptor = os.open(path, os.O_RDWR)
                os.close(descriptor)
                access[name] = "read_write_open"
            except OSError as error:
                access[name] = type(error).__name__
        result["data_access"] = access
    args.output.write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
    return 0 if result["status"] == "checked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
