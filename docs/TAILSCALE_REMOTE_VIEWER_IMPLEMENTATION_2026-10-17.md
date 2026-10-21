# Tailscale 遠端裝置與即時觀看者監控實作紀錄

日期：2026-10-17
範圍：repository implementation、隔離測試；未建置、安裝或重啟正式 Host。

## 使用者說明

Dashboard 已採本機優先說明：

> MeowKeeper+ 以本機運作為核心，預設不將監控服務公開到網際網路。出門在外時，可以透過兩端安裝官方 Tailscale 建立加密的私人連線，安全地連回家中觀看貓咪。

說明同時避免暗示一定是點對點：Tailscale 會嘗試直接連線；網路不允許時可能使用 Tailscale 加密中繼。這兩者都不等於 Funnel／公開網際網路服務。
觀看仍需通過 MeowKeeper+ Dashboard 登入，攝影機與 go2rtc 管理 API 不因此公開。

## 接線完成

- 唯讀解析本機官方 `tailscale status --json` 的 Self／Peer 清單；不執行登入、`tailscale up`、Serve、Funnel 或 Tailnet 掃描。
- 清理對外裝置欄位；NodeKey 不回傳、不保存。裝置以穩定雜湊參照識別；本機別名儲存於資料根 `state/tailscale-device-aliases.json`，僅供 Dashboard 顯示。
- Tailscale CLI 狀態使用 10 秒 single-flight cache，讓同時開啟的分頁共用一次讀取。
- 新增受登入保護的唯讀狀態／裝置 API，以及 CSRF 保護的本機別名更新 API：
  - `GET /api/v1/tailscale/status`
  - `GET /api/v1/tailscale/devices`
  - `PUT /api/v1/tailscale/devices/{device_ref}/alias`
- Dashboard 將 Tailscale online、Tailscale active、近期 Dashboard session、即時觀看連線分開呈現。Online／Active 不會被當成「正在看畫面」。
- Dashboard 使用狀態以已驗證 session 與實際 ASGI socket peer 記錄；不採信 `X-Forwarded-For` 等可由請求者提供的轉送標頭。無法配對的來源標示為未知，不推測設備名稱。
- JPEG 只有在成功回傳有效影格後才列為觀看；MSE 只有在代理收到實際媒體 bytes 後才列為觀看；WebRTC 只有在 peer 連線成功後列為觀看。影片連線關閉、session 登出或紀錄逾時後會移除／清除活動。
- 預覽仍使用原有共用來源，不為狀態頁新增 RTSP producer、FFmpeg 或轉碼工作。
- 設定頁／新手引導說明兩端安裝、私人加密連線、直連與加密中繼、以及本機監控預設不公開的邊界。

## 隔離驗收結果

| 驗收 | 結果 | 證據 |
|---|---|---|
| 全 Python 測試 | passed | `.venv` `python -m unittest discover -s tests -v`：859 tests，246.977 秒，OK |
| Tailscale／觀看者與 MSE／WebRTC 定向測試 | passed | 33 tests，4.394 秒，OK |
| Python lint | passed | `.venv` Ruff `check src tools tests`：All checks passed |
| Python 編譯 | passed | `.venv` `compileall -q src tools tests` 無錯誤 |
| 瀏覽器端 runtime 測試 | passed | `tests/js/*.cjs` 六個測試全部通過，包含共享預覽、MSE、非同步預覽、AI overlay 等 |
| 真實 Tailscale CLI／登入狀態 | not_checked | 本輪沒有查詢使用者主機 Tailnet 或執行中的正式 Host |
| 兩地網路連線、直連／加密中繼路徑 | not_checked | 需要主機與外出裝置在不同實際網路上測試 |
| 異地瀏覽器的 Dashboard／MSE／WebRTC 播放 | not_checked | 隔離測試不等於真實 Tailnet、瀏覽器或網路驗收 |
| 建置包／安裝版／可攜版 | not_checked | 本輪沒有建置或執行安裝器 |

## 安全與計數定義

- 「裝置在線」取自官方 CLI；「Tailscale 有活動」取自 CLI 的 `Active` 欄位；這兩個欄位都不代表 Dashboard 被使用。
- Dashboard session 是近期已驗證登入工作階段；同一裝置多分頁可形成多條播放器連線，但裝置數仍以匹配的 peer 去重。
- 播放器僅在媒體成功流動時進入即時觀看計數；未知／代理來源保留為未辨識數，不以 `localhost` 或 forwarded header 猜成遠端設備。
- 活動追蹤只保存在程序記憶體並採有界／逾時清理；裝置別名是唯一持久化的新增資料，無 Tailscale NodeKey、Auth Key、Token 或尾網 IP 暴露在裝置 API 回應中。

## 下一步實機驗收

1. 在家中主機與外出手機／電腦登入同一 Tailnet，確認官方 CLI 裝置清單與 Dashboard 別名呈現。
2. 從不同網路登入 Dashboard，核對來源是否配對到正確 Tailnet peer；若經代理，應顯示未辨識而非錯配。
3. 實際觀看 JPEG／MSE／WebRTC，逐一確認觀看狀態只有在成功影格／媒體流動時出現，關閉分頁後於逾時內消失；同時檢查上游攝影機仍只有必要的單一 producer。
4. 依 Tailscale 管理端／診斷可得資訊確認該次使用直連或加密中繼。MeowKeeper+ 不自行猜測連線路徑。

未完成上述實機步驟前，功能狀態為「repository 接線與隔離驗收完成；異地實機驗收待進行」，不得宣稱已在使用者網路成功遠端觀看。
