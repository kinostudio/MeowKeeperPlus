"""官方 Tailscale 安裝引導；不自動下載、安裝或登入。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class InstallerPlan:
    status: str
    source_url: str
    version: str | None = None
    file_size_bytes: int | None = None
    sha256: str | None = None
    requires_confirmation: bool = True
    message_zh_tw: str = ""


class OfficialTailscaleInstaller:
    official_host = "pkgs.tailscale.com"

    def plan(
        self,
        *,
        source_url: str | None = None,
        version: str | None = None,
        file_size_bytes: int | None = None,
        sha256: str | None = None,
        confirm: bool = False,
    ) -> InstallerPlan:
        url = source_url or "https://pkgs.tailscale.com/stable/"
        host = urlsplit(url).hostname
        if host != self.official_host or "latest" in url.lower():
            return InstallerPlan("error", url, message_zh_tw="必須使用官方固定版本來源，不接受 latest 或第三方鏡像。")
        if (
            not version
            or file_size_bytes is None
            or file_size_bytes <= 0
            or not sha256
            or len(sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in sha256)
        ):
            return InstallerPlan("not_checked", url, message_zh_tw="尚未提供固定版本、檔案大小與 SHA-256 manifest。")
        return InstallerPlan(
            "ready_to_download" if confirm else "confirmation_required",
            url,
            version=version,
            file_size_bytes=file_size_bytes,
            sha256=sha256.lower(),
            requires_confirmation=not confirm,
            message_zh_tw="安裝前需由使用者確認並顯示 Windows UAC；MeowKeeper+ 不會靜默安裝。",
        )
