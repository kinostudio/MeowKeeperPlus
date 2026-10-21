# MVP Dashboard：YOLO 偵測與通知接線報告

日期：2026-08-03

## 已完成

- `tools/run_host.py` 現在建立 `RepositoryCoreController` 時會接入既有的
  `TelegramNotifier`、Chat ID 與 `EventClipService`。
- Telegram 採明確 opt-in：只有傳入 `--enable-telegram` 才會讀取
  `MEOWKEEPER_TELEGRAM_BOT_TOKEN` 與 `MEOWKEEPER_TELEGRAM_CHAT_IDS`；未傳入旗標時
  即使環境變數存在也不會外傳。
- `tools/launcher.py` 會將上述安全旗標轉送給正式 Host；Token 不進命令列。
- Host 預設分析器仍為 `yolo`，預設模型為 `models/yolo11n.pt`。既有的 YouTube
  adapter、FFmpeg ingest、YOLO cat-only 分析、事件 MP4 與非同步 Telegram 佇列未重寫。

## 本地真實乾跑

使用 repository `.venv`、本地含貓影片 `reports/phase3a-smoke/cat120.mp4`、
`--analyzer yolo` 啟動 Dashboard。實際產生：

- `data/dashboard-manual-preview/latest.jpg`：存在，65,369 bytes
- `data/dashboard-manual-events/event_*.mp4`：1 個，478,184 bytes
- SQLite `media_events`：1 筆 `cat_detected`
- 事件信心值：0.9249
- clip 狀態：`completed`
- Telegram 狀態：`disabled`（本次未設定外傳憑證，未向外傳送）
- `/healthz`：HTTP 200

Dashboard 已開在 `http://127.0.0.1:8000/`；目前受既有管理者登入保護，瀏覽器保留
在登入頁供使用者手動登入。測試來源影片播放完畢後，需由 Dashboard 的「開始監控」
或「重新啟動監控」再次啟動來源。

## Telegram 正式啟用方式

在本機環境設定 Token 與逗號分隔 Chat ID 後，使用：

```powershell
.\.venv\Scripts\python.exe tools\run_host.py --enable-telegram --open-browser
```

或：

```powershell
Start-MeowKeeperPlus.cmd --enable-telegram
```

若未設定其中任一值，Host 會以繁體中文錯誤停止，不會嘗試猜測、重試或洩漏秘密。

## 驗證結果與限制

- 全部 unittest：152 passed
- 變更檔案 Ruff：passed
- compileall：passed
- Telegram 真實 Chat ID：本輪未測試
- YouTube 長時間 Dashboard：本輪未重做；Phase 3D 已有公開網址解析與 JPEG smoke
- MX250 GPU 與家庭 RTSP：仍需實機驗收
- Xiaomi/go2rtc 串流核心：本輪未修改
