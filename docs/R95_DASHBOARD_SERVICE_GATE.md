# r95：首頁共享預覽與服務操作

## 本輪範圍

- 首頁和即時畫面使用同一個 shared-preview.js，讀取既有 ingest JPEG／metadata；不建立攝影機或 FFmpeg producer。
- 首頁依 Stitch 參考布局接線三個狀態卡、事件回報、系統摘要、CPU 歷史曲線、24 小時事件柱狀圖及 Telegram 回條統計。
- 首頁截圖／前 15 秒保存沿用 `/api/v1/live/save`，PTZ／隱私操作導向即時畫面。
- 右上角「啟動／停止監控」沿用既有 lifecycle API。依使用者最新確認，停止的是完整影音管線，不是只停 AI；Dashboard／Tray 保留。
- 更多操作：重啟服務、關閉程式。完整關閉沿用 Tray 的 ownership-verified helper，不依通用程序名稱批次終止。
- 重啟時記住本次是否正在監控；已停止時以 `--disable-monitor` 重新執行，避免偷偷重開來源。
- 沿用 r94 安裝入口的 runtime-lock 修正；新發布包不得包含使用過的私人資料。

## 原始現況與驗收界線

使用者回報 r94 已安裝，ProgramData manifest 亦為 r94；但本輪實際 8000 owner 是原 dist/r94 的 embedded Python PID 17440，入口為該包 run_host.py，healthz core_status=stopped。
此可攜包沒有 web_admin.json，所以它顯示首次設定。正式 ProgramData/Data/web_admin.json 仍存在，未覆蓋、未重建帳號。
已提供使用者只停止此已識別舊 Host 並啟動正式 Core 排程的代碼。不得把首次設定的可攜版當成正式攝影機驗收。
之後實際正式 Host 已接手：8000 PID 34780、go2rtc 1984／8554 PID 34740；頁尾 r94／正式安裝版。正式首頁可讀 1920×1080 JPEG，來源連線 1，預覽約 4.8～4.93 FPS，AI 約 3～3.6 FPS，ONNX Runtime CUDA。
同時開啟首頁及即時畫面時，go2rtc 活動串流 producer=1、consumer=1；證據只保留計數，未保存完整來源 URL。
這證明正式 r94 已有影格，不代表 r95 新版 UI 已安裝驗收；r95 仍由使用者自行安裝。

隔離 UI 使用合法本地測試貓圖，Banner 明確說明不是攝影機、不推論、不發送 Telegram。已有可讀 JPEG、24 個柱狀圖區段及空資料提示；未使用假成功率或假事件。
Figma connector 在本輪以正確 link_id 呼叫仍回報 Unknown tool；完整逐頁 Figma 一致性未驗收。
Tray 原生視覺 probe 尚未取得可供擷取的 target window；單元測試不代替原生文字驗收。未宣稱修補包或正式 Tray 已通過。

## 測試

全套測試與最後建置校驗見 `reports/r95-tests-final.txt` 及本輪建置輸出。
全套 565 項通過（152.910 秒）；Ruff／compileall／Node 語法通過。390px 的 12 個子頁均有共享操作及版本頁尾，無水平溢出。首頁隔離桌面／手機截圖位於 `reports/r95-home-desktop.jpg`、`reports/r95-home-mobile.jpg`，不冒充正式攝影機。
發布包 `dist/MeowKeeperPlus-portable-2026-10-13-r95` 完成嚴格校驗，20,573 個檔案，private_data_present=false，YOLO SHA-256 未變。
內附 Python 真正啟動隔離 Host（18895／獨立資料／不連攝影機）：停止 API 通過、停止後 Dashboard 可讀、關閉 API 正常退出。這次沒有在真實 Tray 活動時執行完整關閉，因此不替代原生 Tray E2E 驗收。
秘密掃描規則只命中兩處 RTSP 遮罩樣板／redaction regex，未發現實際 Token 或帶帳密的來源 URL；不以 regex 原始命中數冒充秘密數。
隔離瀏覽器採 localhost、正式頁採 127.0.0.1：只換 port 無法隔離 Cookie，兩個同主機服務的 session 會互相覆蓋。
額外 topbar 測試驗證停止 API 呼叫完整 controller stop，且 Dashboard 狀態仍可存取；兩頁使用共同 preview module。
相關 Ruff、compileall 及 Node 語法檢查已執行。真正正式攝影機、Tray 文字與逐頁 Figma 仍須另外通過，不宣稱全部完成。
