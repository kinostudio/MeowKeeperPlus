"""驗證一個 Telegram Bot；只有明確指定 --send-test 才會傳送訊息。"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .probe_common import ProbeItem, ProbeReport, ProbeStatus, emit_report
except ImportError:
    from probe_common import ProbeItem, ProbeReport, ProbeStatus, emit_report


def _api_call(token: str, method: str, data: dict[str, str] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    encoded = urlencode(data).encode("utf-8") if data else None
    request = Request(url, data=encoded, method="POST" if data else "GET")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def probe_bot(token: str | None) -> tuple[ProbeItem, dict[str, Any] | None]:
    if not token:
        return (
            ProbeItem(
                "telegram_bot",
                ProbeStatus.NOT_CHECKED,
                "未提供 Telegram Token；請設定指定的環境變數。",
            ),
            None,
        )
    try:
        payload = _api_call(token, "getMe")
    except HTTPError as exc:
        return (
            ProbeItem(
                "telegram_bot",
                ProbeStatus.ERROR,
                "Telegram 拒絕 Token；請回到 BotFather 確認 Token。",
                {"http_status": exc.code},
            ),
            None,
        )
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return (
            ProbeItem(
                "telegram_bot",
                ProbeStatus.ERROR,
                "無法連線 Telegram API；請檢查網路後再試。",
                {"error_type": type(exc).__name__},
            ),
            None,
        )

    result = payload.get("result", {})
    safe_details = {
        "id": result.get("id"),
        "username": result.get("username"),
        "can_join_groups": result.get("can_join_groups"),
        "supports_inline_queries": result.get("supports_inline_queries"),
    }
    return (
        ProbeItem("telegram_bot", ProbeStatus.OK, "Telegram Bot Token 驗證成功。", safe_details),
        result,
    )


def send_test_message(token: str | None, chat_id: str | None, enabled: bool) -> ProbeItem:
    if not enabled:
        return ProbeItem(
            "telegram_test_message",
            ProbeStatus.NOT_CHECKED,
            "未指定 --send-test，因此沒有傳送外部訊息。",
        )
    if not token or not chat_id:
        return ProbeItem(
            "telegram_test_message",
            ProbeStatus.ERROR,
            "傳送測試訊息需要有效 Token 與 --chat-id。",
        )
    try:
        payload = _api_call(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "喵喵守護者+ Phase 0：Telegram 測試訊息傳送成功。",
            },
        )
    except HTTPError as exc:
        return ProbeItem(
            "telegram_test_message",
            ProbeStatus.ERROR,
            "Telegram 拒絕測試訊息；請確認 Chat ID、先傳送 /start，並檢查 Bot 權限。",
            {"http_status": exc.code, "chat_id": chat_id},
        )
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return ProbeItem(
            "telegram_test_message",
            ProbeStatus.ERROR,
            "測試訊息傳送失敗；請檢查網路後再試。",
            {"error_type": type(exc).__name__, "chat_id": chat_id},
        )
    message_id = payload.get("result", {}).get("message_id")
    return ProbeItem(
        "telegram_test_message",
        ProbeStatus.OK,
        "測試訊息已成功傳送。",
        {"chat_id": chat_id, "message_id": message_id},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="驗證一個 Telegram Bot；只有 --send-test 會傳送訊息。"
    )
    parser.add_argument(
        "--token-env",
        default="MEOWKEEPER_TELEGRAM_BOT_TOKEN",
        help="保存 Bot Token 的環境變數名稱。",
    )
    parser.add_argument("--chat-id", help="選擇性的測試 Chat ID。")
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="明確允許傳送一則繁體中文測試訊息。",
    )
    parser.add_argument("--output", help="選擇性 JSON 報告輸出路徑。")
    return parser


def collect_report(
    token: str | None,
    chat_id: str | None = None,
    send_test: bool = False,
) -> ProbeReport:
    bot_item, _ = probe_bot(token)
    message_item = send_test_message(token, chat_id, send_test)
    return ProbeReport("telegram", [bot_item, message_item])


def main() -> int:
    args = build_parser().parse_args()
    token = os.getenv(args.token_env)
    emit_report(collect_report(token, args.chat_id, args.send_test), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
