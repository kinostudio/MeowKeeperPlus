# 自適應推論／全螢幕／逐軌跡回饋驗收（2026-10-09）

## 本輪原始碼變更

- `core/inference/runtime_device.py` 共用一次性 CUDA 故障降級：只處理 GPU 相關
  RuntimeError；重建 Ultralytics predictor、模型移至 CPU，同一影格重試一次。
  CPU 重試失敗仍向上回報；不記錄原始例外文字，避免洩漏來源資訊。
- YOLO detector 與 BoT-SORT tracker 均接上降級。後續保持 CPU，不反覆重試 GPU。
- V0.2 Host adapter 同步實際後端；裝置改變時重建 session track namespace，
  不將短期追蹤編號重用為永久身分。
- 即時預覽 fullscreen 使用整個視窗與 contain，不增加攝影機連線或重新解碼。

## 已驗證與限制

- 相關 37 項 unittest 通過；含真實貓／非貓 YOLO、Telegram disabled、逐軌跡
  假 Telegram 整合、V0.2 後端同步與全螢幕 template/CSS 契約。
- 新增真實 YOLO 故障注入復原測試後 `tests.test_real_yolo` 共 5 項通過。
  此測試模擬 GPU 失敗但 CPU 重試為真實模型，不等同 GPU 硬體驗收。
- 本地自動裝置實測：PyTorch `2.13.0+cpu`、CUDA unavailable、CPU、含貓圖 2 隻。
  此為原 CPU runtime 基線，不代表後續獨立 CUDA runtime 的實測結果。
- 後續建立 `runtime/python-cuda`，保留原 CPU runtime；官方固定版本
  torch `2.13.0+cu126`／torchvision `0.28.0+cu126` 安裝成功，未更動系統
  Python、驅動或系統 CUDA。pip 來源及 wheel hash 記錄於 ignored
  `reports/cuda-runtime-install.json`。
- RTX 2060 真實自動推論通過：模型參數 `cuda:0`、貓圖 2 隻、非貓圖 0 隻；
  明確 CPU 模式也通過。真實 GPU 推論後注入一次 CUDA 錯誤，重試裝置
  `cuda:0 → cpu`，CPU 模型參數及真實貓咪偵測均通過。這是受控故障注入，
  不是自然 GPU 故障或 MX250 硬體驗收。
- 可重複驗證入口：`tools/probe_adaptive_inference.py`；`--device cpu` 驗證
  CPU，`--test-fallback` 驗證真實 GPU 後的受控降級。工具不接攝影機或 Telegram。
- 同一 CUDA runtime 以子程序 `CUDA_VISIBLE_DEVICES=-1` 隱藏 GPU 後，
  `cuda_available=false`、自動選用 CPU、模型參數 CPU、貓 2 隻／非貓 0 隻均通過；
  沒有修改系統環境或驅動。這不等同另一台無 GPU Windows 電腦的安裝驗收。
- 全套回歸 406 項通過（127.786 秒；啟動後另新增的真實 GPU 故障注入復原
  測試已獨立通過，未冒充納入該次全套計數）。Ruff 與 compileall 通過。
- 正式 `/healthz` HTTP 200；僅證明服務健康，不證明最新程式已部署。
- 正式 `/live` 起初導向登入頁，稍後使用者完成登入；寵物頁顯示兩隻已登錄、
  參考照片／特徵分別 7/7 與 8/8。沒有繞過登入取得私人事件或憑證。
- Telegram 頁真實狀態：enabled、runtime configured、兩位已保存收件人；
  點擊測試文字後顯示確認框，確認操作遇到 CDP 逾時。傳送結果 unknown，
  不重複觸發；沒有以此宣稱送達成功或真實 callback 驗收通過。
- 本輪沒有重啟 Host、修改正式 SQLite 或偽造 callback。
- 後續真實 Telegram 驗收：逐貓摘要已送達，點擊「回報軌跡 1」後 Bot 確實送出
  獨立軌跡選單。正式逐軌跡回條由 3 增至 6；標籤與歷史仍為 0。
  對尚未確認 cat_presence 的軌跡選「身分不確定」，收到「此回饋無效或已過期」；
  程式與 SQLite 原始碼證明身分／行為／動作必須先有 cat_presence=cat。
  此為前置條件拒絕，不是已證明回條過期；提示需更明確，不能宣稱落庫已通過。
- 目前摘要沒有附對應軌跡截圖，前一支事件影片與摘要時間不同，不能用前一段影片
  冒充此軌跡的正確身分或動作。仍需可對照的逐軌跡影像證據與真實標註。

## 回饋驗收缺口修正（尚待正式部署）

- 新增 `core/notifications/track_evidence.py`，把當次追蹤器已有 crop JPEG
  組為 Track 1／2 等編號對照；最多 20 格，缺圖留空、不冒用其他貓咪影像。
- 影像組合與送出在既有有界通知 worker；只向 opt-in 使用者送出。原始摘要及
  回條綁定仍使用摘要 message_id，不以截圖回條混淆事件。
- 明確標示截圖時間與「當次觀測不代表整段行為」，不把單張截圖当成完整行為證據。
- 尚未確認 cat_presence 就標註身分／行為／動作時，顯示先按「這條是貓咪」的
  提示；仍保持原先 SQLite 安全前置條件。
- 27 項相關測試與局部 Ruff 通過，包括兩貓編號、空白格、損壞影像、容量上限、
  opt-in 圖片分發與前置條件拒絕不落庫。真實圖片送達與真實逐軌跡標籤仍待驗收。
- r68 已完成建置（19,300 檔），不宣稱包含這批較晚修改。r69 另行建置，不改寫 r68。
- 本批修正後全套 410 tests 通過（136.545 秒），全範圍 Ruff 與 compileall 通過。
  測試中 Ultralytics 自動在開發環境補裝 pi-heif；此為額外相依動作，非系統
  Python／驅動／CUDA 變更，不以此宣稱正式包所有影像格式均已驗收。
- r69 完整性驗證通過（19,301 檔、private_data_present=false），四個 runtime／通知
  修正檔與 repository hash 相同。升級命令被安全審查拒絕，正式服務未被更換；
  原因為舊安裝器包含停止本機所有 FFmpeg 的跨專案副作用。
- 已限縮升級清理為安裝目錄內的 FFmpeg 完整路徑，保留 PID、建立時間與命令列
  複核。3 項 installer 測試通過，含真實 Windows PowerShell 載入函式、模擬
  本專案／其他專案程序的選取測試；沒有以此冒充真實 SYSTEM 升級通過。
  此安全修正另建 r70；r69 不被覆寫。
- r70 建置與內附 verifier 通過（19,301 檔、private_data_present=false）。包內
  安裝器 SHA-256：`AC310E9E2164B80BEC395669373DE26887E405EBBC3A54A2A2AF21CC013F752A`。
  已確認沒有全域停止 FFmpeg 呼叫，與原始碼正規化換行後內容相同；建置器加入
  UTF-8 BOM／CRLF，故不以位元雜湊不同誤判程式版本不同。
- 2026-10-09 19:09 正式 r70 安裝器已啟動（PID 13812）；UAC consent.exe
  PID 19240 存活等待使用者核准。觀測時正式 manifest 仍為 r66。
  可恢復執行 session：51590；不得再次啟動另一個升級程序或宣稱已部署。
- 後續 session 51590 已終止（exit 1），Windows 回報 RunAs 操作被使用者取消。
  UAC／安裝器原 PID 已消失，正式 manifest 仍是 r66，Dashboard HTTP 200。
  沒有再次觸發 UAC；下一個必要操作是由使用者在管理員 PowerShell 執行已驗證
  r70 安裝器。上述 session 僅為歷史證據，不再是可等待的 live handle。

## 尚需完成，不能以 Mock 替代

1. 真實 GPU 與 CPU 本地推論已通過；無 GPU 的發布包啟動及 MX250 實機仍待驗收。
2. 發布／部署本輪程式，確認正式 Host 實際後端與即時全螢幕顯示。
3. 真實 Telegram 逐貓摘要送達回條；逐軌跡按鈕實際點擊，分別回報身分、行為、
   動作，確認正式 SQLite event/track、標籤與不可覆寫歷史一一對應。
4. 回饋須使用既有白名單與 opt-in；不得注入假 Telegram update 冒充人工點擊。

目前總目標保持未完成；身分、行為準確度仍為 experimental。

## 真實追蹤降級補驗（2026-10-09）

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。

## r71 發布包驗證結果（2026-10-09）

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。

## r71 正式部署與真實回饋補驗

- 使用者完成升級後，正式 manifest 為 r71；Host PID 9964，healthz HTTP 200，
  core_status=running，TCP 8000 listener 為 0.0.0.0。
- 真實 Telegram 已收到 21:50:02 編號對照圖及 21:49:57 逐貓摘要。
  瀏覽器截圖確認 Track 1 是貓咪，不是同場人類；實際點擊最新回報軌跡按鈕，
  Bot 送出選單，再實際提交 cat、identity unknown 與 behavior unknown。
- 正式 SQLite 唯讀檢查：同一事件具有四維標籤 cat_presence=cat、identity=saki、
  behavior=unknown、motion=stationary，四筆均有對應 Telegram 事件回條；
  歷史包含 cat 2 筆、identity unknown／saki 各 1 筆、behavior unknown 1 筆、
  motion stationary 1 筆。最終 saki／stationary 非本次代理提交，不冒稱為代理
  已確認的身分／整段動作；保留原始歷史，不為驗收覆寫人工回報。
- 新版真實通知與 SQLite 回饋鏈已有證據；仍未證明兩隻貓各自正確標註。
- Dashboard 登入 Session 已失效，不能繞過驗證。正式全螢幕與即時後端 metrics
  尚待使用者重新登入後驗收；未宣稱全部目標完成。
