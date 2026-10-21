# MVP Phase 3A：Web Dashboard Bring-up Report

日期：2026-08-02
範圍：讓 Web Dashboard 真正啟動、可見並接上 Phase 2；沒有新增模型、ONVIF、PTZ、Tailscale、Xiaomi 或行為種類。

## 交付與啟動方式

- 正式 Host：`tools/run_host.py`
- 相容別名：`tools/run_web.py`（轉呼叫 Host，不再建立 interface-only façade）
- Windows 啟動檔：[Start-MeowKeeperPlus.cmd](../Start-MeowKeeperPlus.cmd)
- Tray 啟動檔：[Start-MeowKeeperPlus-Tray.cmd](../Start-MeowKeeperPlus-Tray.cmd)
- 預設網址：`http://127.0.0.1:8000/`
- 乾淨啟動：`python tools\run_host.py --open-browser`
- 有來源時可設定 `MEOWKEEPER_MEDIA_SOURCE_URL`；沒有來源仍會啟動 Dashboard 並顯示「尚未設定來源」。

Host 只綁定 localhost，Uvicorn 啟動後先輪詢 `/healthz`，成功才開啟瀏覽器。關閉瀏覽器或 Tray 不會停止 Host；Uvicorn shutdown 才會依序關閉 controller、FFmpeg／事件工作與 SQLite。

## Web Runtime 實際版本

已在專案 `.venv` 執行 `pip install -e ".[runtime,web,tray]"`，沒有修改系統 Python、驅動、CUDA 或 FFmpeg：

| 套件 | 實際版本 |
|---|---:|
| FastAPI | 0.115.14 |
| Starlette | 0.46.2 |
| Uvicorn | 0.34.3 |
| Pydantic | 2.13.4 |
| Jinja2 | 3.1.6 |
| python-multipart | 0.0.32 |
| itsdangerous | 2.2.0 |
| httpx（TestClient） | 0.28.1 |
| pystray | 0.19.5 |
| Pillow | 11.3.0 |

FastAPI 版本範圍已固定為 `<0.116`，避免最新 Starlette TestClient 變成未宣告的 `httpx2` 依賴。

## 實際接線

`RepositoryCoreController`（`src/meowkeeper/app/core_controller.py`）接上：

- `FfmpegMediaIngest`：唯一來源程序，同時輸出 JPEG 與壓縮 MPEG-TS 分段。
- `DiskSegmentRingBuffer`：Phase 2 壓縮環形快取。
- `YoloFrameAnalyzer` 或安全的 `NoopFrameAnalyzer` fallback。
- `AnalysisPipeline`：貓咪事件與冷卻。
- `EventClipService`／`ClipAssembler`：事件 MP4、SQLite metadata、非同步 Telegram。
- 既有 `SQLiteStore`：事件列表與授權影片路徑。

Web route 只呼叫 `CoreStateFacade`；Tray 只呼叫 localhost API。預覽使用 controller 保存的既有 ingest JPEG，沒有新增第二條 RTSP 或 FFmpeg 輸入。開始監控重複按下時回報既有 session，不建立第二條來源連線。

## 驗收證據

### 自動測試

- `python -m unittest discover -s tests -v`：**104 passed**，FastAPI smoke 已實際執行，不再 skipped。
- `ruff check --no-cache src tools tests`：**passed**。
- `python -m compileall -q src tools tests`：**passed**。
- JSON／TOML：`pyproject.toml` 可由 setuptools parser 讀取；現有 JSON 契約測試通過。

### 真實 Uvicorn／HTTP smoke

使用專案 `.venv` 啟動真正的 Uvicorn 子程序（非 TestClient），隔離資料庫與管理員雜湊至 `reports/phase3a-smoke/`：

| 檢查 | 結果 |
|---|---|
| `/healthz` | HTTP 200 |
| `/` 首次流程 | 導向 `/setup` 並取得 HTML |
| `/setup` | HTTP 200；一次性 CSRF setup token |
| `/api/v1/auth/setup` | HTTP 200；再次 setup 會拒絕 |
| `/api/v1/auth/login` | HTTP 200；HttpOnly session／CSRF cookie path=`/` |
| `/dashboard` | HTTP 200；含「喵喵守護者+」「MeowKeeper+」「監控狀態」 |
| `/events` | HTTP 200；由授權 API 讀取 SQLite，無事件時顯示中文空狀態 |
| `/api/v1/status` | HTTP 200；回報 core status 與 metrics |
| `/static/style.css`、`/static/app.js` | HTTP 200 |
| 未登入事件 API | HTTP 401 |

實際 server log：[host3.out.log](../reports/phase3a-smoke/host3.out.log)。本機沒有可用 Edge／Chrome headless binary，因此 `reports/dashboard_home.png`、`dashboard_events.png`、`dashboard_mobile.png` 為 `not_checked`；沒有建立假截圖。

完成 UI／生命週期修正後，另以隔離資料庫在 port `8129` 啟動新的 Uvicorn process：`/healthz` HTTP 200、`/dashboard` HTTP 200，HTML 含「喵喵守護者+」；測試程序已正常停止，沒有遺留 Python Host process。

### Phase 2 本地媒體接線 smoke

以測試用 `cat.jpg` 產生的本地 H.264 影片啟動 `RepositoryCoreController`（`analyzer=noop`，不外傳 Telegram）：

- controller state：`running`
- source connections：`1`
- FFmpeg restart count：`0`
- ring buffer：實際掃描到 2 個 MPEG-TS 分段、約 2 秒、約 512 KiB
- ingest JPEG snapshot：存在
- `/live`／`get_recent_clip(2, 0)`：產生可讀 MP4，實測因分段時間戳可得到約 50.2 秒；這個本地檔案快速輸入造成的時間差仍需以真實 RTSP／YouTube 來源校正
- 再次 `start_monitor()`：回傳「監控已在執行中」，沒有第二個 session

## 頁面與資料真實性

| 頁面 | 狀態 |
|---|---|
| 總覽／Dashboard | 已可見；核心狀態、來源、FFmpeg、AI、ring、Telegram、SQLite、CPU／RAM、GPU／磁碟卡片由 API 更新 |
| 即時畫面 | 已接現有 ingest JPEG；沒有來源時顯示中文提示 |
| 事件影片 | 已讀 `media_events`，分頁、事件類型／Telegram 狀態篩選、clip 存在與否、授權 MP4 endpoint |
| setup／login | 已接 PBKDF2、一次性 setup CSRF、HttpOnly／SameSite session、失敗限制與逾時 |
| 設定頁 | 入口與受保護 API 已存在；專用表單仍是 placeholder |
| 診斷頁 | 讀取真實 `/api/v1/diagnostics` |
| 日誌頁 | 只說明保存位置；完整日誌瀏覽仍是 placeholder |
| Windows Tray | localhost client、Host health wait、單一實例與 pystray menu（狀態、開始／停止／重啟、畫面／最近片段、資料夾、診斷匯出、網址複製）已接線；本次無可用桌面顯示器，圖示 smoke `not_checked` |

## 尚未完成／需要實機

- 真實 D-Link RTSP 554 路徑、YouTube Live、音訊、長時間 FFmpeg watchdog 與斷線恢復。
- 真實 YOLO 推論裝置；本次 Web bring-up 使用 noop local media smoke，不能宣稱 GPU。
- 真實 Telegram Chat ID、影片上傳與外網重試。
- MX250 8 GB／16 GB 長時間 CPU／RAM／VRAM 量測。
- Windows 桌面 pystray 圖示與 Edge／Chrome 截圖。
- Windows 發布包內含 Python runtime、FFmpeg、套件與模型的正式建置／安裝驗收。
- 設定專用表單、完整日誌列表與家庭 12～24 小時驗收。

因此 Phase 3A 的「可啟動、可見、可登入、可讀取 Phase 2 事件、可由 API 播放授權 MP4」已由自動測試與本機 Uvicorn 驗證；**仍不可宣稱 MeowKeeper+ 已完成家庭 MVP 對外發布**。
