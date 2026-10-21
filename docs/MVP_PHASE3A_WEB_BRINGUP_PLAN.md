# MVP Phase 3A：Web Dashboard Bring-up Plan

## 真實阻塞原因

目前 repository 已有 FastAPI app factory、Jinja2 dashboard 與 localhost Tray client，但仍有四個可觀察缺口：

1. `.venv` 沒有 FastAPI、Uvicorn、Pydantic 或 pystray，因此上一輪 smoke test 被依賴缺少條件跳過。
2. `tools/run_web.py` 只建立 `CoreStateFacade`；沒有啟動既有 `FfmpegMediaIngest`、YOLO／noop 分析、`EventClipService` 或 ring buffer。
3. `CoreStateFacade` 沒有可執行的核心生命週期，沒有來源時也無法顯示「等待設定」的真實狀態。
4. Dashboard 只有單一模板，尚未提供 setup/login、事件影片播放與低負載既有 JPEG 預覽的完整可見流程。

## 本次修改範圍

- `src/meowkeeper/app/core_controller.py`：建立單一 `RepositoryCoreController`，重用 Phase 2 元件並保證同一時間只有一個監控 session。
- `src/meowkeeper/app/web/main.py`：提供正式 `create_app(core_controller, settings)` 入口。
- `src/meowkeeper/app/web/api.py`、`session.py`、`state.py`：補上 FastAPI lifespan、healthz、setup/login、事件模擬、真實狀態與安全檔案服務。
- `tools/run_host.py`：唯一正式 Host 啟動入口，localhost 綁定、可選來源、健康檢查後開啟瀏覽器。
- `Start-MeowKeeperPlus.cmd`、`Start-MeowKeeperPlus-Tray.cmd`：不含使用者絕對路徑的 Windows 啟動檔。
- Web templates/static：提供可見總覽、即時預覽、事件、設定、診斷與日誌頁面。

不修改 YOLO、事件組片、SQLite migration、Telegram notifier 或來源解析的既有實作；不新增第二條攝影機來源連線。

## 啟動流程

1. `run_host.py` 讀取可選的 `MEOWKEEPER_MEDIA_SOURCE_URL`、建立 SQLite 與 controller。
2. FastAPI app 建立並由 Uvicorn 綁定 `127.0.0.1`。
3. Host 等待 `/api/v1/health` 成功後，依 `--open-browser` 開啟 `http://127.0.0.1:8000`。
4. 沒有來源時保留 Dashboard 可用並顯示「尚未設定來源」；提供來源時才由 Web 的「開始監控」或 Host 的自動啟動建立單一 ingest。
5. 初次使用者由 `/setup` 建立管理員，之後 `/login` 取得 HttpOnly session 與 CSRF cookie。

## 核心與 Web 邊界

- Host 程序持有 FFmpeg、YOLO／noop、ring buffer、事件影片、SQLite 與 Telegram dispatcher。
- Web route 只呼叫 `CoreStateFacade`，facade 再呼叫 `RepositoryCoreController`；route 不直接建立媒體、模型或資料庫寫入。
- Tray 只使用 `http://127.0.0.1:8000/api/v1/`，不持有來源或 Telegram secret。
- Dashboard 預覽沿用同一個 ingest 產生的 JPEG；瀏覽器不得直接拉 RTSP，也不得建立第二條來源連線。
- 核心關閉瀏覽器或 Tray 後仍繼續運作；Host 關閉時才依序停止 controller、FFmpeg、事件工作佇列與 SQLite。

## 驗收方式

- 安裝 repository 已聲明的 runtime/web/tray extras 到專案 `.venv`，驗證 import 版本。
- 以真實 Uvicorn server 執行 `/healthz`、HTML、CSS/JS、setup/login、狀態、事件與影片授權路由。
- 使用本地影片來源驗證單一 session、JPEG 預覽與 Phase 2 SQLite 事件列表；沒有來源時驗證可安全啟動。
- 執行既有 unit tests、Ruff、compileall、JSON/TOML 驗證；若 Windows 桌面或瀏覽器不可用，明確記錄 `not_checked`，不把介面存在誤報成實機驗證。
