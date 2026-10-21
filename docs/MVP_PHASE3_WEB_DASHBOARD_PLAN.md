# MVP Phase 3：Web Dashboard、Tray、遠端與小米邊界計畫

## 目的與基線

本階段建立「操作與驗證中心」，不重寫已通過測試的 Phase 2 媒體、YOLO、SQLite、事件影片或 Telegram 核心。正式產品架構為 headless 24H 核心、FastAPI、server-rendered Dashboard、Windows Tray 與可選 Tailscale Serve。

目前已確認：

- `FfmpegMediaIngest` 是單一來源入口，同一個 FFmpeg 程序輸出低 FPS JPEG 與壓縮 MPEG-TS 分段。
- 壓縮分段由 `DiskSegmentRingBuffer` 保存，事件影片由 `EventClipService` 組裝並寫入既有 `SQLiteStore`。
- Telegram 影片通知已是獨立有界佇列；Dashboard 不得直接建立新的來源、FFmpeg 或 Telegram 連線。
- Phase 3A 已在專案 `.venv` 安裝並固定 Web／Tray runtime；實際版本與 smoke 證據見 `docs/MVP_PHASE3A_WEB_BRINGUP_REPORT.md`。

## 資料流與責任邊界

```text
單一來源 → FfmpegMediaIngest
             ├─ JPEG → 既有 AI／預覽狀態
             └─ MPEG-TS → RingBuffer → EventClipService → SQLite／Telegram

CoreStateFacade ← 既有核心生命週期與唯讀 metrics
       ↑
FastAPI /api/v1 + SSE → Dashboard（本地資源）
       ↑
Tray（僅 localhost API client，不持有秘密）
```

Web 層只讀取核心狀態、事件分頁與已授權的事件檔案。高畫質預覽先保留 `restream_url`／go2rtc 介面；在沒有既有本地 restream 時，只提供最後一張 JPEG 狀態圖，不重新拉取 RTSP。未來接入高畫質預覽時，必須由單一 ingest 或本地 go2rtc 分流，並以觀看者計數啟停資源。

## API 與安全

- API 版本固定為 `/api/v1`；健康檢查只回報程序狀態，不回傳秘密。
- 預設只綁定 `127.0.0.1:8000`，不提供 LAN、Funnel 或 `0.0.0.0` 預設值。
- Phase 3E 可由非秘密 `config.json.dashboard` 明確啟用 LAN；啟用後只將 Dashboard
  bind 改為 `0.0.0.0`，不改變現有 port。變更需重新啟動 Host；go2rtc API／RTSP
  仍固定 localhost，且不由瀏覽器直接連線。
- 以應用程式 token／Bearer 驗證保護操作 API；token 由環境變數或未來 Credential Manager reference 取得，絕不進 HTML、SQLite、日誌或 URL。
- 目前實作提供 PBKDF2 管理者密碼、HttpOnly／SameSite Session Cookie、CSRF header／cookie、
  登入失敗限制、Session timeout 與登出；首次設定仍需由受保護的 credential 初始化，未驗證前不宣稱正式公開發布。
- 事件影片只透過授權 API 讀取，路徑必須位於設定的事件根目錄內；不掛載整個 `data/events` 為靜態目錄。
- 區網網址只由已登入 API 動態列出啟用且非虛擬介面的私有 IPv4；不保存 IP、不寫入秘密，
  並排除 loopback、APIPA 與常見虛擬介面。

## 併發與降級

- SSE 使用每一客戶端有界佇列，最多每秒一次狀態更新；無訂閱者時不啟動更新工作。
- 事件列表以 SQLite `LIMIT/OFFSET` 分頁；Dashboard 請求不逐影格讀取資料庫。
- API、SSE、Tray 逾時或失敗只影響操作介面，不影響 FFmpeg、AI、環形快取、SQLite 寫入或 Telegram 佇列。
- `start/stop/restart` 先經 Core facade；Phase 3A 的 `RepositoryCoreController` 已接上既有 Phase 2 元件。沒有來源時回報 `waiting_for_source`，不偽造已啟動的來源。

## Tray

Tray 使用 pystray（可選）或無 GUI 的 CLI fallback；只呼叫 localhost API、開啟瀏覽器、複製網址與顯示繁體中文錯誤。使用 Windows named mutex／測試用 lock file 防止多開。Tray 結束不呼叫停止核心；停止服務需明確二次確認。

## Tailscale 與 Xiaomi

- Tailscale 模組只呼叫官方 CLI 的唯讀狀態與明確的 Serve 計畫；不自動安裝、登入、使用 Auth Key、開 Funnel 或修改 ACL。Serve 變更先保存狀態並需明確確認。
- Xiaomi/go2rtc 模組只建立官方來源、版本／雜湊 manifest 與 localhost bridge 的探測契約；沒有帳號、型號、韌體或合法 release 時回報 `not_checked`。Thingino、SD 卡與刷機全部保留 TODO。

## 驗證順序

1. Python 單元測試、Ruff、compileall。
2. 專案 `.venv` 安裝 `.[web,tray]` 後，執行 FastAPI TestClient、Dashboard smoke、Tray client smoke。
3. 以本地影片確認 Dashboard 沒有第二條來源連線；真實 YouTube、D-Link RTSP、Tailscale、Xiaomi、MX250 與 30 分鐘以上整合壓力測試分開標示 `not_checked`。

## Phase 3E 區網存取驗收

- `dashboard.lan_access_enabled=false` 時只以 `127.0.0.1` bind；`true` 時才使用
  `0.0.0.0`，並由受保護設定頁提示重啟。
- 預覽頁每個瀏覽器最多以 2 FPS 取得既有 ingest JPEG；分頁隱藏時停止輪詢，離開時
  清理物件網址，不建立新的攝影機連線或 FFmpeg producer。
- Windows 防火牆只呼叫 `netsh advfirewall firewall` 的固定規則、TCP Dashboard Port、
  `profile=Private`；命令使用 `shell=False` 且權限不足只回報狀態。
