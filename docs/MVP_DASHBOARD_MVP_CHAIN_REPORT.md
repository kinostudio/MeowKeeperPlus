# Dashboard MVP：來源→YOLO→事件→Telegram 邊界報告

日期：2026-08-04

## 已接線

- `/settings/source` 合併來源與攝影機入口。
- YouTube、RTSP、本機影片與 Mock 的非秘密來源設定保存至 `config.json.media`。
- Host 啟動時依序使用 CLI、來源環境變數或 `config.json` 來源。
- 單一 `RepositoryCoreController` 建立單一 `FfmpegMediaIngest`。
- JPEG 影格送入預設 YOLO11n；只保留 COCO cat class 15。
- 事件送入既有壓縮 ring buffer、事件 MP4、SQLite 與 Telegram 背景佇列。
- Telegram 啟用狀態與憑證設定可由 Dashboard 保存；Bot Token／Chat ID 進入 Windows
  Credential Manager，DPAPI 為 fallback，環境變數仍可供可攜式／診斷模式使用。

## 真實本地驗證

執行來源：`reports/phase3a-smoke/cat30.mp4`；分析裝置：CPU；模型：`models/yolo11n.pt`。

- FFmpeg 取得影格：通過。
- YOLO 找到 2 隻貓，最高信心約 0.92：通過。
- 事件數：1。
- 事件 MP4：通過，實際約 1.2 秒。
- SQLite `media_events`：保存 `cat_detected`、信心值、MP4 路徑與 hash：通過。
- Telegram：本次未提供 Token／Chat ID，狀態為 `disabled`，沒有外傳：符合安全預設。

## 公開 YouTube 驗證

使用既有公開 Big Buck Bunny 測試頁面，以固定版 yt-dlp／yt-dlp-ejs 解析並取得媒體
metadata 與第一個來源影格；本次使用 `noop`，因素材不是貓影片，不產生 cat event。
簽名媒體 URL 未保存於設定、SQLite 或日誌。

## 尚未完成

- D-Link 真實 RTSP 帳密與家庭網路串流：`needs_real_data`。
- 家庭 YouTube 直播長時間重連：`needs_real_data`。
- Telegram 真實 Bot／Chat ID 事件傳送：`needs_real_data`。
- Telegram inbound webhook：需要外部 HTTPS 公開入口，localhost MVP 尚未實作。
- MX250 實機推論後端與長時間壓力：`not_checked`。

本報告不把本地乾跑或公開影片 smoke test 宣稱為 24 小時家庭 MVP 驗收。

## 本輪收尾驗證（2026-08-04）

- 完整 unittest：168 tests，全部通過。
- 變更範圍 Ruff：通過；全 repository Ruff 仍有既有
  `tools/phase3c5_xiaomi_trace.py` 的 9 個 lint 問題，未在本輪無關重構。
- `python -m compileall -q src tools tests`：通過。
- 前端本地 URL 掃描：未發現頁面、JavaScript 或 CSS 寫死的 localhost／127.0.0.1 URL。
- 秘密模式掃描：未發現 Bot Token、RTSP userinfo、serviceToken 或 ssecurity 原值。
- 實際渲染 `/settings/source`：共用導覽只有一份，包含「設定」「維護」「來源與攝影機」，並以同一頁整合來源與攝影機入口。
- Dashboard 實際健康狀態：`0.0.0.0:8000`，`/healthz` HTTP 200；本機正式 go2rtc 仍為
  `127.0.0.1:1984`／`127.0.0.1:8554`。
- 收尾端到端乾跑：`reports/mvp-chain-final/`；60 frames、CPU、YOLO11n，找到 2 隻貓，
  產生 1 個有效 MP4（1.2 秒、254340 bytes），SQLite event 為 `completed`，Telegram 為
  `disabled`。
- 公開 YouTube adapter 收尾 smoke：固定 yt-dlp 解析成功，取得第一個媒體來源；yt-dlp、
  yt-dlp-ejs、FFmpeg、FFprobe 與 Node runtime 均可用，且 `reports/mvp-youtube-final/` 未保存
  `googlevideo` 或簽名來源網址。

### 通知邊界

目前已接通的是事件產生後的 Telegram Bot API outbound queue（文字／圖片／MP4），
而且預設關閉、Token 與 Chat ID 不寫入設定檔；Dashboard 可將其安全保存到本機憑證邊界。真正由 Telegram 反向呼叫本機的 inbound
webhook 需要外部 HTTPS 公開入口，未在本輪假裝已完成；因此「Telegram webhook 完全接通」
仍是 `not_checked`／待另行設計的部署項目。

## 來源啟動與 Host 重啟修正（2026-08-03）

本輪針對「選擇 RTSP／YouTube 後按保存後開始監控，來源又回到 Mock」完成最小修正：

- 原因是舊版按鈕只呼叫 `/api/v1/monitor/start`，沒有先提交表單；尚未另按保存時，
  `config.json` 仍是 Mock，Host 重新讀取後自然回到未設定來源。
- `source.js` 現在由「保存後開始監控」先執行同一個來源設定 PUT，再呼叫 monitor start，
  並在狀態更新前保留按鈕防重入。
- 頂部導覽新增「重新啟動 Host」。它受既有 Session／CSRF 保護，請求只會讓目前 Host
  清理媒體與資料庫資源，再以同一 repository `.venv` 絕對 Python 路徑重新載入最新程式與
  `config.json`；Windows `execv` 可能產生新 PID，這不代表重複 Host，鎖與 port owner 仍會
  阻擋重疊程序。
- Telegram 設定若只有 `enabled=true` 而未提供 Token／Chat ID，Host 會安全保持通知關閉，
  不會因缺少可選秘密而讓 Dashboard／媒體管線啟動失敗。Dashboard 保存的憑證會在
  Host 重啟後讀取；明確使用 CLI `--enable-telegram` 時仍可使用環境變數。

本輪驗證證據：

- 真實 8000 端口 smoke：臨時 Token 只存在於測試程序環境；POST `/api/v1/host/restart`
  回應 `restarting`，重啟後 `/healthz` HTTP 200，新的 owner 仍是 repository `.venv`。
- 正式 Host 已恢復為 `.venv\Scripts\python.exe tools\run_host.py --no-open-browser`
  並持有 8000；正式 go2rtc 仍為 localhost 1984／8554。
- 完整 unittest：168 tests，全部通過；變更範圍 Ruff 與 `compileall` 通過。
- 本機設定目前保留公開 YouTube 測試頁作為來源；Telegram `enabled=false`，未設定秘密，
  因此本輪沒有對外傳送。
- 正式 Host 恢復後以相同公開 YouTube 頁面自動啟動：preview `latest.jpg` 持續更新，
  ring 子目錄產生 34 個壓縮 MPEG-TS 分段（約 14.4 MB），Host 子程序中只有 1 個
  repository `vendor\ffmpeg\bin\ffmpeg.exe` producer；最新影格可讀取。測試素材是兔子影片，
  不應產生貓咪事件。

本輪沒有把家庭 D-Link RTSP、家庭 YouTube 直播、真實 Telegram Chat ID 或 inbound webhook
誤標示為已完成；這些仍需使用者登入 Dashboard 後以自己的非秘密設定完成實機驗收。
