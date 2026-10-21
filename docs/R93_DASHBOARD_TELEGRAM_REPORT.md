# r93 Dashboard／Telegram 改版驗收

日期：2026-10-13；正式 Host 未由本輪自動安裝或重啟。

## 已實作

- 共用奶油白／暖橘／虎斑棕布局，分組導覽、手機收合、側欄維護子項、來源與攝影機入口保留。
- 首頁單一共享 JPEG、影格／AI／通知三狀態、事件卡、CPU／RAM 資源曲線與資料時間範圍。
  新增預覽只讀取既有影格，不新增 ingest；沿用 5 秒狀態輪詢，資源歷史每分鐘最多讀取一次。
  頁面隱藏時不更新預覽；未連線來源的舊影格不當成連線成功。
- `/api/v1/system/status` 重用 Telegram `RuntimeStatusService`：硬體與一小時採樣納入同一快照，
  由 Telegram 文字及 Dashboard 使用；缺失感測器與資料皆不補零。API 保持登入保護。
- 共用頁尾由目前執行目錄的 build manifest 取得版本／日期／執行方式。
  開發版、可攜版、正式安裝版與資料未提供各有明確文字，不寫死 r93。
- Telegram 同列「回饋／通知／冷卻」。回饋單擊切換；通知內開／關，或數字靜音小時；
  冷卻按鈕後輸入數字分鐘。只對已授權私人對話生效；數字上下文五分鐘過期，可取消。
  開啟通知會解除個人暫時靜音；不取消個人冷卻。`/cd`、`/mute`、`/feedback` 舊指令保留。
- operator 以上可「儲存位置／位置一／位置二」。每台相機獨立輪流覆寫兩個位置，
  座標讀回成功才保存；回到位置要求讀回一致。不能讀回／不支援絕對移動時不宣稱成功。
  新 `telegram-position-presets.json` 不含 DID 原值、不覆寫隱私位置。真實控制仍為 not_checked。
- Web 事件人工回報：個體／數量／非貓 → 每隻行為／動作 → 確認保存。
  共用已存在的行為及動作 taxonomy，動態寵物姓名；不把影片級標籤冒用為某條軌跡。
  Web Session／CSRF 獨立於 Telegram 收件者 opt-in；Web 回報不改任何收件者偏好。
  SQLite schema 15 新增 append-only Web 修訂；相同 request ID 冪等重試，舊 revision 回報 409。
  歷史可看到 Web 及 Telegram 來源，跨通道貓咪正誤不同時標記待釐清。
  此次不新增自動重訓，也不將 Web 回報自動轉為身分特徵；仍需要既有特徵品質驗收流程。

## 已驗證

- 完整 unittest：556 passed（`reports/r93-unittest.txt`）；模板微調後另跑 4 個模板測試 passed。
- Ruff、compileall、Dashboard／回報 JavaScript 語法檢查 passed。
- 真實 Windows 瀏覽器、隔離 18893 測試站：1440／768／360px 總覽與390px事件頁未水平溢出；
  360px 導覽 Tab／Escape 收合 passed；同頁只有一張共享預覽及兩張資源圖表。
- 完整狀態面板確認有硬體、一小時使用率／溫度及缺值文字。
- 隔離示例回報由瀏覽器操作「兩隻都在 → 貓一行走／貓二進食 → 確認保存」，收到修訂 1。
  單元測試另外驗證雙貓各自欄位、修訂保留、重跑 migration、重複提交及 CSRF 拒絕。
- 截圖 `reports/r93-ui-home-desktop.jpg`、`r93-ui-home-mobile.jpg`、`r93-ui-review-desktop.jpg`。
  全部為醒目標示的隔離示例，不是家庭攝影機／真實 Telegram 的完成證據。

## 仍未驗證／限制

- Stitch 指定螢幕無權限；Figma `12:5860` 結構與截圖皆因 Starter MCP 額度上限拒絕。
  本輪以使用者提供的 V3 交接包、HTML 與色票截圖實作，不能宣稱逐像素比對 Figma 已通過。
- 未在此輪傳送真實 Telegram 設定選單或控制實體攝影機；部署後需使用者驗收。
- 圖表為真實資源採樣，不是完整的寵物活動時間分布或通知投遞聚合。
- Web 歷史暫不包含所有逐軌跡 Telegram annotation 維度；此次 Web 提交屬影片級人工標籤，
  並不取代既有 Telegram 逐軌跡／證據學習管線。
- 未宣稱取得 Awwwards／Webby／FWA 獎項級認證或全機型、全瀏覽器實機驗收。

## 升級

使用者自行執行 r93 發布包的 `Install-MeowKeeperPlus.cmd -Upgrade`。
正式資料仍在獨立 Data 目錄；本輪不替使用者觸發 UAC、不停止正式監控。
