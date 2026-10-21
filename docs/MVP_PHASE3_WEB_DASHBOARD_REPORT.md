# MVP Phase 3 Web Dashboard／Tray 報告

## 本階段交付

已建立 FastAPI `v1` API façade、server-rendered HTML/Jinja2 Dashboard、原生 CSS／JS／PWA
manifest、Tray localhost client／單一實例鎖、Tailscale 官方 CLI 邊界與 Xiaomi/go2rtc
安全規劃介面。既有 Phase 2 媒體、事件影片、YOLO、SQLite 與 Telegram 核心未重寫。

目前完整 unittest 為 104 tests；Phase 3A 已將 Web／Tray runtime 安裝到專案 `.venv`，
FastAPI TestClient smoke 已實際通過。Phase 3A 的完整 bring-up 證據與新增 Host 接線請見
`docs/MVP_PHASE3A_WEB_BRINGUP_REPORT.md`。

## 狀態分類

| 項目 | 狀態 | 證據／限制 |
|---|---|---|
| API 路徑、token／CSRF、事件分頁、事件檔案路徑檢查 | 自動測試與 FastAPI smoke 已驗證 | 家庭來源與長時間負載仍需實機 |
| Dashboard 10 個頁面入口、local static assets | 已建立 | 前端操作仍依受保護 API 與 runtime extra |
| 無第二條來源連線的預覽邊界 | 已由架構契約驗證 | 高畫質 restream 尚未接 go2rtc |
| Tray API client／localhost 限制／二次確認／單一實例 | 純契約測試已驗證；pystray 已安裝 | Windows 桌面圖示仍需實機 smoke |
| Tailscale CLI 狀態與 Serve 計畫 | Mock／interface | 目前未確認 Windows CLI、登入、MagicDNS 或 Serve |
| Xiaomi/go2rtc bridge | 官方 go2rtc runtime＋安全設定介面；真實 discovery 已接線 | go2rtc v1.9.14 與兩個 Xiaomi 裝置已由 session 讀取；profile/影像因 `no p2pid allocated` 仍待恢復 |
| Web CPU／RAM 負載 | not_checked | 已可啟動 server，但尚未執行長時間 MX250 負載測量 |
| Tailscale 遠端 URL | not_checked | 只有本機固定 URL 與安全計畫 |
| go2rtc 版本／來源／授權／SHA-256 | 已驗證 | 官方 AlexxIT/go2rtc v1.9.14；manifest 與實際 localhost process smoke 已保存 |
| Xiaomi 登入／影像取得 | 帳號 session／裝置 discovery 已確認；影像 `degraded` | 使用者需在官方 Mi Home 重啟受影響攝影機，再由 Dashboard 明確觸發 profile probe |
| MX250／D-Link RTSP／YouTube／Telegram Chat ID | not_checked | 需家庭實機驗收 |

## 安全與未完成事項

- 預設只允許 `127.0.0.1`；沒有 Funnel、LAN 預設監聽、公開 CORS 或靜態事件資料夾。
- API token、CSRF、Tray token 與 Xiaomi/Tailscale 秘密不進 HTML／日誌／報告。
- 已加入記憶體型 PBKDF2 管理者密碼、HttpOnly／SameSite Session Cookie、CSRF cookie、
  登入失敗限制、Session timeout 與登出契約；正式 Windows 發布仍需由受保護的 credential
  初始化帳號，且本階段不宣稱可公開發布。
- 未執行實際 go2rtc binary、Tailscale Serve、Xiaomi 登入、WebRTC、高畫質預覽、30 分鐘
  Web＋核心壓力或家庭 12～24 小時驗收。

## 下一個驗收入口

建置者準備含 `fastapi`、`uvicorn`、`pydantic`、`jinja2`、`pystray` 與 Pillow 的發布 runtime
後，可執行：

```powershell
$env:MEOWKEEPER_WEB_TOKEN = "由管理者安全產生的 token"
# 或在開發／驗證環境以環境變數初始化 Session（密碼不放命令列）
$env:MEOWKEEPER_WEB_ADMIN_USERNAME = "admin"
$env:MEOWKEEPER_WEB_ADMIN_PASSWORD = "由管理者安全提供的長密碼"
python tools\run_web.py --host 127.0.0.1 --port 8000
```

Phase 3A 使用 localhost 首次 setup/login 流程；Token、密碼與 Session 不會寫入報告。正式
發布仍須由建置者提供內含 runtime 的 Windows 包，不要求終端使用者執行 pip。

## 自動驗證摘要

- `python -m unittest discover -s tests -v`：104 tests passed，FastAPI smoke 已執行並通過。
- `ruff check src tools tests`：passed。
- `compileall -q src tools tests`：passed。
- JSON 與 `pyproject.toml`（setuptools parser）驗證：passed。
- 真實 YOLO／Phase 2 本地影片測試：沿用既有報告；本階段未修改其主路徑。
