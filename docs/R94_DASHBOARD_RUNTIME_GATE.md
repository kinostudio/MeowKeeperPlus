# r94：首頁圖表、預覽與 Tray 修正驗收

日期：2026-10-13。此文件區分程式、隔離版型、正式部署與實機證據。

## 原始證據與修正

- 正式 App manifest 為 r93。受保護 diagnostics 實測核心 `error`、`core.last_error=TypeError`、FFmpeg／AI `not_started`、來源連線 0；不是已接通攝影機的證據。
- `_create_analyzer` 傳 `device` 給 `IsolatedTracker`，舊建構子不接受，會在取流前失敗。r94 接受並傳至 worker；測試保留這個實際呼叫契約，不再只有替身 tracker。
- 首頁 `app.js` 未版本化可能沿用舊狀態事件契約。所有共用資源以當前 manifest cache_key 載入；图表自行初始化，不依賴接到影格才有座標區。
- 新鮮度改以 Host `checked_at` 與 `last_frame_at` 比較，避免手機／主機時鐘不同誤判。較慢 AI 完成舊影格時不得倒退 ingest 的最新時間。
- `renderQuality(null)` 原先兩個 optional 值都 undefined 時比較相等，接著讀 `active.quality`，瀏覽器實測 TypeError。r94 明確要求 active.quality 存在。
- 小米設定只保留配對、測試與接入，移除 PTZ 請求函式及方向／停止按鈕；方向、位置與隱私僅在即時畫面。
- Tray popup 恢復 pystray 專用 menu HWND 作為 owner／foreground／WM_NULL target。該窗口交由 DefWindowProc 處理；icon HWND 的 dispatcher 吞掉未處理訊息，不應拿來代替原生選單繪圖窗口。仍保留有限通知佇列、UI loop 更新與非阻塞指令。

## Stitch ZIP 對照

已讀 ZIP 的 `DESIGN.md`、`code.html`、`screen.png`。延續 224px 導覽、米白與暖橘、7:5 預覽／事件、資源摘要。新增兩個實際資料模組：

- 24 個小時區段柱狀圖：只統計 detector 的 cat_detected，排除 simulated；不是貓咪數量、活動精準度或醫療判斷。
- Telegram 送達摘要：按事件聚合，至少存在一個成功 message receipt 才計已確認；等待、失敗、未啟用／靜音分開顯示。不是所有收件人的成功率。
- CPU／RAM 缺資料仍呈現量尺與說明，不補造曲線；僅一次初始化與每分鐘統計讀取，預覽仍用既有共享 JPEG。

## 瀏覽器驗收

- 隔離 UI 127.0.0.1:18894：醒目示例標記、不接攝影機、不推論、不發 Telegram、不改正式 Host。
- 1440px 首頁：共享測試 JPEG 640×480、2 個 canvas、24 個 bins，scrollWidth 1425；390px scrollWidth 375，無水平溢出。
- 390px 逐頁檢查：總覽、即時、事件、趨勢、來源、小米精靈、寵物、Telegram、AI、Tailscale、診斷、日誌、進階設定；均有頁尾與有效標題。檢查時無瀏覽器 error。
- 證據：reports/r94-home-desktop.jpg、r94-home-mobile.jpg、r94-page-checks.json。圖片是隔離測試，不能宣稱家庭攝影機已接通。

## 尚未通過，禁止宣稱完成

- Figma 已能讀畫布及圖層清單，但 MCP get_design_context 目前回報 Unknown tool，未取得高精度逐頁節點資料。沒有完成逐頁像素級 Figma 一致性驗收；本輪主要視覺目標為使用者 ZIP，不將圖層清單等同完成畫板比對。
- Computer Use 已初始化並列出 Windows 視窗，但 Tray／工作列沒有提供可定位的目標窗口；白色無字的真實右鍵畫面尚未重新驗收。單元測試證明 owner／訊息邊界，不是 Windows 視覺成功證據。
- 正式 r93 本輪診斷仍 error，真實首頁／即時頁同源影格與持續推論未通過。r94 不自動安裝、不觸發 UAC、不重啟正式 Host；由使用者升級後再驗收。
- 不用測試圖片或健康檢查代替實機成功。不修改 Xiaomi Session、go2rtc binary 或另開攝影機串流。

## 驗證命令

使用既有 .venv：unittest discover、Ruff、compileall、Node 語法檢查及 portable manifest 驗證。實際結果保存 reports/r94-unittest.txt 與發布包 build_manifest.json；未執行完不得自行填 passed。
