"""官方／go2rtc 登入流程只開啟瀏覽器，不接收帳號密碼。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class XiaomiAccountFlow:
    status: str = "not_started"
    provider: str = "official_or_go2rtc"
    message_zh_tw: str = "請由使用者在官方流程完成 Email、簡訊或 CAPTCHA 驗證。"

    def begin(self, url: str) -> dict[str, str]:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("小米登入流程 URL 格式不正確。")
        safe = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        return {"status": "browser_required", "url": safe, "message_zh_tw": self.message_zh_tw}
