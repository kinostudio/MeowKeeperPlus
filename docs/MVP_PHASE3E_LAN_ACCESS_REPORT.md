# MVP Phase 3E：Dashboard 區網存取驗收報告

日期：2026-08-03
基線：MX250、8 GiB RAM 最低；本機開發機結果不代表 MX250 效能。

## 結論

Phase 3E 的程式與安全邊界已接線，並完成本機 Windows listener、同源前端、Session
保護、共享 JPEG 預覽與 Private-only 防火牆命令的自動化驗證。實際家庭 Wi-Fi 裝置、
iPhone Safari 登入及管理員建立防火牆規則尚未在本輪完成，因此不能宣稱區網發布驗收
全部通過。

## 實作內容

- `src/meowkeeper/app/web/network.py`：非秘密 `DashboardNetworkConfig`、`config.json`
  原子保存、私人 IPv4 動態列舉、listener bind 檢查與 Windows Private 防火牆服務。
- `tools/run_host.py`、`tools/launcher.py`：預設 localhost；明確 LAN 設定才使用
  `0.0.0.0`，健康檢查一律走 loopback；設定變更要求安全重啟，避免第二個 Host。
- `src/meowkeeper/app/web/api.py`、`state.py`、`main.py`、`contracts.py`：受保護的
  Dashboard 網路／防火牆 API、設定驗證與動態 URL 狀態。
- Web templates/static：移除前端絕對 localhost URL；即時畫面沿用既有 ingest JPEG，
  每個可見頁面最多 2 FPS，分頁隱藏時停止輪詢。
- `config.example.json`、README、架構／安全／測試文件已標示 LAN 預設關閉及實機限制。

## Listener 與資源證據

### 未啟用 LAN

- 隔離 Host PID `7348`，Port `8011`，listener `127.0.0.1:8011`。
- `/healthz` = HTTP 200；未登入的 `/api/v1/status`、預覽 JPEG、事件影片與診斷 API
  均回 HTTP 401。
- 測試完成後以 repository command line／role 安全停止；沒有終止其他 Python。

### 啟用 LAN

- 隔離設定：`reports/phase3e/lan-config.json`，`lan_access_enabled=true`、Port `8012`。
- Host PID `14016`，listener `0.0.0.0:8012`；本機 `/healthz` = HTTP 200。
- 動態私人網址：`http://192.168.31.40:8012`（只保存於本機測試輸出，未寫入正式設定）。
- 未登入的 Dashboard API、JPEG、事件影片與診斷 API 均回 HTTP 401；沒有將秘密放入 URL。
- go2rtc 未被改動：正式 `1984/8554` 由 PID `35176` 監聽 `127.0.0.1`；隔離
  `21984/28554` 由 PID `27044` 監聽 `127.0.0.1`。

### 共享預覽／單一 producer

使用本地長影片與 `--analyzer noop` 的實際 Host PID `14016`：

| 情境 | HTTP 預覽請求 | Host CPU（程序百分比） | RSS | source connections |
|---|---:|---:|---:|---:|
| 一台瀏覽器等效請求 | 12 | 100.0% | 52,871,168 bytes | 1 |
| 兩台瀏覽器等效請求 | 24 | 98.4% | 52,658,176 bytes | 1 |

兩種情境均取得 HTTP 200 JPEG；FFmpeg restart count = 0，程序樹只有一個 Host 與一個
`vendor/ffmpeg/bin/ffmpeg.exe` producer（PID `12576`）。這證明 Dashboard 預覽沒有因
client 數量增加而建立第二條來源或 producer。這是本地檔案乾跑，不是 D-Link／YouTube
長時間實機證明。

## 安全與驗證結果

- Session Cookie：HttpOnly、Path `/`、SameSite=Strict、HTTP 模式未強制 Secure、沒有
  `Domain=localhost`；登入保護維持既有密碼／CSRF／Session timeout。
- 前端 templates/static 的絕對 `127.0.0.1`、`localhost`、`ws://`、`http://` URL 掃描：
  0 命中；API、圖片與影片使用同源相對路徑。
- 防火牆單元測試確認命令只含固定規則、Dashboard TCP Port 與 `profile=Private`，不含
  go2rtc `1984/8554`；本機實際狀態為 `not_configured`，本輪沒有未經使用者確認建立規則。
- 生產程式／文件秘密掃描：0 個真實憑證命中；測試中的 `secret`、文件中的示例帳密均為
  明確 placeholder，未送出網路。

## 測試命令

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`：本輪完整測試 159 項（
  先前 158 項，加上 Phase 3E Cookie contract）；執行前一輪 158 項全部通過，新增
  `tests.test_phase3e_lan_access` 7 項通過。
- `.\.venv\Scripts\python.exe -m compileall -q src tools tests`：通過。
- 變更範圍 Ruff（network、Host、launcher、Web、Phase 3E 測試）：通過。
- 全 repository Ruff：仍受既有 `tools/phase3c5_xiaomi_trace.py` 的未使用 import／
  broad exception lint 阻擋；本輪沒有修改 Xiaomi 診斷檔，避免違反範圍限制。
- `tools/launcher.py --status-json`、`run_host.py --help`：通過並顯示 LAN 設定選項。

## 尚未驗證項目

- `needs_real_data`：iPhone Safari／Android／平板由實際 Wi-Fi 連線登入、登出後逐項
  驗證頁面、API、SSE、JPEG、MP4、診斷及服務控制。
- `needs_admin`：Windows Private Profile 防火牆規則真實建立、移除及外部裝置連線；
  本輪只做唯讀狀態與 mock command contract。
- `not_checked`：Wi-Fi 改變後重新計算、實體防火牆封包邊界、MX250 負載、YouTube／D-Link
  RTSP 長時間來源與 12～24 小時穩定性。

Xiaomi Cloud、go2rtc、miss_get_vendor、RTSP port bridge 與既有模型／通知核心沒有在本輪
修改。

## 本輪完成後的本機程序

為保留使用者可繼續開啟 Dashboard，最後以正式預設設定啟動 launcher（未啟動 Tray、
未設定來源）：

- Launcher PID `22316`、Host PID `12172`，兩者 executable 均為 repository
  `.venv\Scripts\python.exe`。
- Dashboard `127.0.0.1:8000` 由 Host PID `12172` 監聽。
- go2rtc PID `35176` 仍只監聽 `127.0.0.1:1984`／`127.0.0.1:8554`；隔離 Xiaomi
  go2rtc PID `27044` 仍只監聽 `127.0.0.1:21984`／`127.0.0.1:28554`。
- 第二次 launcher 呼叫未建立第二個 Host；既有 Host PID 維持不變。Tray 本輪以
  `--no-tray` 留在停用狀態，避免把手動驗收與背景程序混在一起。
