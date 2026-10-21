# Tailscale 私人遠端整合

## 裝置與 Dashboard 觀看狀態（2026-10-17）

Dashboard 以本機運作為核心：預設不把監控服務公開到網際網路。出門在外時，主機與
觀看裝置都安裝並登入官方 Tailscale，建立加密的私人連線；Tailscale 會嘗試點對點連線，
若網路條件不允許，可能改走其加密中繼，兩者都不是公開 Funnel。觀看前仍須通過
MeowKeeper+ Dashboard 登入。不要將攝影機帳密或 go2rtc 管理 API 暴露到 Tailnet／公網。

新增的唯讀裝置總覽透過官方 CLI `tailscale status --json` 讀取本機與 peer；只把經清理的
識別、裝置名稱、作業系統、線上／活動狀態與最近出現時間提供給已登入 Dashboard。裝置識別
為本機穩定雜湊參照；自訂別名只寫在 MeowKeeper+ 本機資料，不會修改 Tailnet 裝置名稱，
也不會寫入 Tailscale。UI 優先顯示本機別名，其次 DNS 短名／主機名，未取得時使用「未知裝置」。
狀態採 10 秒單一快取，避免多個 Dashboard 分頁重複呼叫 CLI。

裝置狀態不等於正在看畫面：`online` 是 Tailscale 回報可達、`active` 是 Tailscale
近期活動，均不是 Dashboard 使用證據。Dashboard session、成功取得的 JPEG 預覽、以及 MSE／
WebRTC 已建立並仍有資料的播放器分別計數；以已驗證 session、實際 socket peer IP 與
Tailscale peer 位址比對。反向代理標頭（如 `X-Forwarded-For`）不作為身分依據。無法對上的
連線只顯示為來源未辨識，不猜測裝置。離線／不完整狀態不會被誤報為完整裝置清單或零個觀看者。

驗收界線：新增單元與 API 測試通過，不代表本機 Tailscale 已登入、異地裝置可連、MSE／
WebRTC 可經真實 Tailnet 播放或 relay 路徑已實測。本次不建置、不安裝、不重啟正式 Host；
實際 Tailnet 與陌生網路驗收仍為 `not_checked`。細節見
[2026-10-17 裝置與觀看者監控實作報告](TAILSCALE_REMOTE_VIEWER_IMPLEMENTATION_2026-10-17.md)。

## 支援邊界

MeowKeeper+ 不重新實作 VPN。Windows 上由官方 Tailscale Client 負責登入、服務與 Tailnet
網路；本程式只偵測官方 CLI、顯示版本／登入／IP／MagicDNS／Serve 狀態，並在使用者明確
確認後產生或執行只把 Tailnet HTTPS 轉送到 `http://127.0.0.1:8000` 的 Serve 命令。

預設安全值：

- FastAPI 只監聽 `127.0.0.1`。
- Funnel、一般網際網路公開存取、完整 go2rtc 管理介面與 SQLite／設定資料夾均不公開。
- 不把密碼、Auth Key、Node Key、Token 或登入 URL 秘密參數交給 Python 或寫入日誌。
- 未安裝時只提供官方來源安裝計畫；不自動下載、安裝或顯示 UAC 以外的靜默流程。

## Serve 與 Subnet Router

啟用 Serve 前先讀取既有狀態，保存可恢復資訊並要求二次確認。失敗不影響本機 Dashboard。
Subnet Router 僅接受使用者明確指定的窄 CIDR，提醒需在 Tailscale 管理頁批准；不修改 ACL，
也不預設分享 `192.168.0.0/16` 或 `192.168.31.0/24`。

## 驗證狀態

目前程式碼與安全邊界可由 fake CLI 測試；本機尚未確認 Tailscale 安裝、登入、MagicDNS、
Serve URL、Windows 服務或實際遠端連線，因此均為 `not_checked`。不得把本文件視為已完成
遠端驗收。

## Dashboard 啟動摘要與區網網址

Host 可在 Dashboard `/healthz` 成功後，經由既有有界 Telegram outbound
dispatcher 傳送可選的啟動摘要。此選項 `telegram_startup_notice` 預設關閉，
只有使用者在已登入 Dashboard 明確啟用並重新啟動 Host 才會送出。

摘要只包含：

- 實際偵測到的私有 LAN IPv4 Dashboard URL。
- 本機 `127.0.0.1` Dashboard URL。
- 官方 Tailscale CLI 回報的登入／IP／遠端 Dashboard 可用狀態。
- 來源類型與 AI 設定狀態。

`0.0.0.0` 僅是 Dashboard 的 wildcard bind，不會被當成分享網址。LAN URL
每次由啟動中的介面重新計算，明確排除 loopback、link-local、Tailscale
100.64.0.0/10 與常見虛擬網卡。Python 不執行 `tailscale up`、`serve` 或
`funnel`；Tailscale 安裝、登入與 Serve 仍由使用者使用官方工具管理。
