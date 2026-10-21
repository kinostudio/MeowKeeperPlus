# MVP Phase 3B：Launcher、Dashboard、Tray 與官方 go2rtc 報告

更新日期：2026-08-02（Asia/Taipei）

## 結論

Phase 3B 可測的 repository-local 部分已完成並通過自動驗證：Windows 啟動入口、既有 `.venv`
runtime probe、Host health、Web Dashboard 實際 Edge 截圖、真實 pystray backend smoke、
recent clip 時鐘修正，以及官方固定版 go2rtc runtime 均已接線。

小米真實帳號登入、實際裝置清單、畫質探測與家庭攝影機畫面仍為
`WAITING_FOR_USER_XIAOMI_LOGIN`，不以 Mock 結果宣稱完成。

## Launcher 修復證據

- 原始 `Start-MeowKeeperPlus.cmd`：750 bytes、非 ASCII、LF-only、無 BOM；中文／編碼破損文字被
  CMD 當成殘缺命令，造成 `'e"' is not recognized`、`'\\python.exe' is not recognized` 與路徑錯誤。
- 修正後架構：`Start-MeowKeeperPlus.cmd` → `scripts/Start-MeowKeeperPlus.ps1` →
  `tools/launcher.py` → `tools/run_host.py`。
- 修正後 CMD：ASCII-only、CRLF、無 UTF-8 BOM，支援 `%~dp0`、任意目前工作目錄、空白路徑與 `%*`。
- `Start-MeowKeeperPlus.cmd` SHA-256：
  `2DB33BA39FE0D07D5F1FD3AB68F08C20866B3A99B8BEE5108F771822905F7BA8`
- `Start-MeowKeeperPlus-Tray.cmd` SHA-256：
  `3571E2936C05D39F182BF14298C3E9D351511842F3DCEEBAE47CA4A9F92330DF`
- PowerShell 腳本以 UTF-8 BOM、CRLF 保存，使用 `$PSScriptRoot`、`Resolve-Path`、`Set-Location -LiteralPath`。
- 實際執行：
  `cmd.exe /d /c "C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\Start-MeowKeeperPlus.cmd --verify-launcher --no-browser"`
- 結果：exit code 0；無 CMD 亂碼命令、無「不是內部或外部命令」、無錯誤殘缺路徑。
- `tools/launcher.py` 只使用 `.venv\Scripts\python.exe`、`shell=False`，缺少 import 時才執行已宣告的
  `.[runtime,web,tray]`；本次 probe 顯示無缺少套件，因此沒有重複 pip。

## Runtime 與 Host

目前 `.venv` probe：

| 套件 | 版本／狀態 |
|---|---|
| FastAPI | 0.115.14 |
| Uvicorn | 0.34.3 |
| Pydantic | 2.13.4 |
| Jinja2 | 3.1.6 |
| python-multipart | installed |
| itsdangerous | 2.2.0 |
| httpx | 0.28.1 |
| pystray | 0.19.5 |
| Ultralytics | 8.4.114 |
| OpenCV | installed |
| NumPy | 2.2.6 |

Host 以 127.0.0.1 啟動，port 8131／8132 smoke 的 `/healthz` 均為 HTTP 200；Host PID 由每次
smoke 動態產生，沒有建立第二條來源連線。正常關閉後沒有留下測試 Host。

## Windows 可攜式包前置條件

本次已在 repository-local `.venv` 安裝並驗證 Web／Tray／YOLO runtime，且 repository 內含
FFmpeg、FFprobe、YOLO11n、Tray assets 與官方 go2rtc binary；終端使用者不需要建立 venv、
執行 pip 或手動下載這些元件。`tools/build_portable.py` 仍要求建置者明確提供官方
embedded Python runtime（預設 `runtime/python`），不會偷偷把開發用 `.venv` 冒充 embedded
runtime，也不會修改系統 Python、驅動或 CUDA。本機 `--dry-run` 因缺少該建置輸入而以繁體
中文安全失敗，故正式可攜式包仍為 `not_checked`；這是發布建置前置條件，不是交給終端使用者
的安裝步驟。

## Dashboard 實際瀏覽器驗收

已找到 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`，使用正式 Host port 8131
的 Edge headless backend 實際產生：

- [dashboard-setup.png](../reports/phase3b/dashboard-setup.png)
- [dashboard-login.png](../reports/phase3b/dashboard-login.png)
- [dashboard-home.png](../reports/phase3b/dashboard-home.png)
- [dashboard-events.png](../reports/phase3b/dashboard-events.png)
- [dashboard-mobile.png](../reports/phase3b/dashboard-mobile.png，390×844)

Dashboard 顯示「喵喵守護者+」、攝影機導覽與尚未登入／尚未設定來源的真實狀態；未以靜態 HTML
代替 Host。

## Tray

- `assets/tray/meowkeeper-ready.ico`、`meowkeeper-waiting.ico`、`meowkeeper-error.ico` 均包含
  16、20、24、32、48、64、128、256 像素圖層。
- `tools/run_tray.py --verify-menu`：12 項選單契約通過。
- `pythonw.exe` 真實 pystray smoke：PID 36088，`--smoke --exit-after-seconds 8` exit 0。
- smoke 結束後 Host `/healthz` 仍為 HTTP 200，符合「退出 Tray 不停止核心」。
- 追加隔離 port 8133 的 `pythonw.exe` 真實 smoke：Tray exit 0，退出後 Host `/healthz` 仍為 200。
- 動態 tooltip／圖示依 ready、waiting、error 狀態更新；單一實例以 Windows named mutex 保護。
- 本次環境未以 notification-area 視覺操作保存 `tray-visible.png`／`tray-menu.png`，因此工作列圖示
  的人工視覺項目仍標示 `BLOCKED_DESKTOP_SESSION`；不影響上述真實 pystray backend smoke。

## Recent clip 時鐘修正

### 根因

本地檔案被 FFmpeg 高速消費時，多個 MPEG-TS 分段的 mtime 幾乎相同；索引以 mtime 建立 wall-clock
區間，導致每個分段都與 `/live` 查詢重疊，2 秒請求串入約 50 秒素材。

### 修正

`DiskSegmentRingBuffer.scan()` 現在依 FFmpeg session 及分段檔名順序建立連續的 session media clock；
不再把檔案 mtime 當影片時鐘。`FfmpegMediaIngest` 關閉時也會釋放 JPEG pipe，避免長時間測試資源洩漏。

新增正式 ingest 測試使用 12 秒 H.264、約 1 秒 GOP：

- `get_recent_clip(2, 0)`：實際時長介於 1～5 秒，未再出現 50 秒。
- `get_recent_clip(5, 0)`：實際時長介於 4～8 秒。
- 無音訊／有音訊、stream-copy 與既有 transcode fallback 測試仍通過。

## 官方 go2rtc

- repository：`https://github.com/AlexxIT/go2rtc`
- release：`v1.9.14`
- asset：`go2rtc_win64.zip`
- executable：`vendor/go2rtc/v1.9.14/go2rtc.exe`
- ZIP SHA-256：`DD4167D75CB04ABE618855B7C71F8658BD009F60C1A71835D134D2C11C939907`
- EXE SHA-256：`923D57252E8139A69C52E4ACC1E399A640244A8EF457FD9B7267A25847D68F8C`
- manifest：`vendor/go2rtc/model_manifest.json`
- 實際程序 smoke：localhost API `127.0.0.1:1984`、RTSP listener `127.0.0.1:8554`，start／health／stop 通過。
- 啟動前會重新核對 EXE 大小及 SHA-256；不符合時拒絕執行。沒有把管理 UI 暴露到 LAN 或 Tailscale。

## 小米設定邊界

已建立：

- 可恢復 `XiaomiSetupService` state machine 與 `data/state/xiaomi-setup.json`。
- Dashboard `/cameras`、`/cameras/xiaomi/setup`。
- 版本化 go2rtc、Tray、Xiaomi API；寫入操作需要 session／CSRF。
- 密碼、CAPTCHA、完整 token、完整來源 URL 不進 state、日誌或 API 回應。
- 裝置 discovery、profile 0～5／auto／sd／hd 與唯一 source switch callback 的介面。

尚未可宣稱：

- Xiaomi 帳號已登入。
- 任何真實裝置／MJSXJ03HL 已載入。
- 實際 profile 解析度、FPS、codec、音訊或 60 秒畫面。
- 小米來源已啟用、事件 MP4 或家庭 RTSP 已通過。

目前唯一人工操作是：在 Dashboard 完成一次本機 Xiaomi Mi Home 登入／驗證；進度保存為
`WAITING_FOR_USER_XIAOMI_LOGIN`。

## 驗證摘要

- unittest：115 passed。
- Ruff：passed。
- compileall：passed。
- JSON／TOML／UTF-8／CMD bytes contract：passed。
- 真實含貓／非貓 YOLO 與 Phase 2 媒體測試：沿用既有測試與報告；本階段未新增模型。
- 真實 go2rtc process：passed。
- 2 小時壓力測試：尚未執行；不可宣稱已通過 24H 或家庭長時間驗收。
- MX250、D-Link RTSP 554、YouTube、Telegram Chat ID：仍需實機驗證。

因此目前可進入「家庭環境前的 12～24 小時實機驗收準備」，但在 Xiaomi 登入、MX250、D-Link／YouTube
來源與 Telegram 真實 Chat ID 完成前，不宣稱對外發布或完整 Phase 3B 完成。
