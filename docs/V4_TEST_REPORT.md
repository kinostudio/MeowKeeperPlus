# V4.3 驗證紀錄（未完成實機驗收）

## 2026-10-15 請求證據／100筆統計／純文字grace合併

主要改動完整回歸：695 tests、155.018秒、OK，保存於 `reports/v43-performance-unittest.txt`。其後的UTC Z解析與送出marker失敗路徑小修正另以局部驗證覆蓋：performance 5 tests、2.470秒、OK；behavior statistics 4 tests、2.695秒、OK；HTTP transport 10 tests、6.565秒、OK；vision settings 8 tests、0.281秒、OK。不將695寫成未重跑的新數量。

另外已驗證semantic revision 8 tests、delivery telemetry 6 tests（含wait過期後不讀DB／身分與不採用晚到候選）、event semantics 7 tests。最新全量Ruff、compileall與JS語法檢查passed。100筆與瀏覽器證據詳見 `V4_SEMANTIC_PERFORMANCE.md`，其中合成平均／百分位不是實機效能。

沒有外部API Key、沒有外送家庭影像、沒有正式Telegram實測或正式Host升級；不以loopback server、synthetic影片或既有message ID當實機完成。

## 2026-10-15 schema22／通知可發送後的時間證據

新增 `test_delivery_telemetry.py`：5 tests、2.062秒、OK。可控時鐘分別驗證0／5／10秒等待在7秒相容處理後開始；多收件者重試只等一次；時序沒有原始Provider例外文字、caption或Chat ID。Python3.10的built-in與Future TimeoutError曾被混分為failed；修正後局部複測通過。`test_web_event_review.py`：7 tests、5.101秒、OK，含同事件時序與401保護。Ruff、compileall、JavaScript node --check passed。

隔離瀏覽器驗證：合成影片review可播放、通知時序4步與本機／AI文案說明可讀；390px無水平溢出（page375、dialog client／scroll320）。白話說明Enter／Escape與焦點返回通過，browser error log為空。這是合成素材與數值，不是實際Telegram或Provider。隔離fixture PID33280已不存在；沒有停止其他程序或改正式Host。

舊完整測試session41580的控制代碼已不存在，沒有最終輸出，不計為通過。重新執行session65910：688 tests、180.441秒、OK（exit0），輸出保存於 `reports/v43-schema22-unittest.txt`。之後追加的不是貓歷史顯示案例單獨執行，最新 `test_web_event_review.py` 8 tests、6.106秒、OK；不將688寫成尚未重跑的689。全量Ruff、compileall passed，JavaScript node --check passed。

## 2026-10-15 schema21／先草稿、後提交

新的原影片與逐Track入口採用持久化身分草稿，不在第一題寫入人工annotation；最後確認時與tag history同交易提交。第一題／後續步驟TTL沿用原通知收件時間，返回身分會清空動作；不是貓需確認但不詢問動作。保存baseline修訂摘要，拒絕外部身分修訂後的舊草稿；保存origin update防重開，舊be格式不能繞過新草稿提交。schema20既有人工歷史仍保留。

局部測試：`test_track_tag_feedback.py` 14 tests、0.126秒、OK；`test_telegram_feedback.py` 19 tests、3.697秒、OK；`test_web_event_review.py` 6 tests、2.637秒、OK。全量Ruff與compileall passed。含真正poller處理格式的FakeTelegram原影片receipt→身分→兩個動作→確認提交；不是實際Bot或家庭影像驗收。

完整回歸 session95031：682 tests、153.904秒、OK（exit0）。之後補上「不是貓」確認頁不得用過期的返回動作按鈕跳回動作題；該局部守衛追加驗證於14項track feedback測試，未將後續局部修改冒充新一次完整回歸。正式Host、家庭資料庫、發布包、真實Telegram未修改或部署。

## 2026-10-14 schema 18／候選歷史

- `.venv\Scripts\python.exe -m unittest discover -s tests -v`：638 項、164.755 秒，1 error。舊行為表 migration 的 BEGIN IMMEDIATE 與提前執行的新候選資料 INSERT 交易衝突；不是 Telegram 外部錯誤。
- 修正：先完成舊表結構遷移，再執行 legacy 候選歷史 INSERT；不刪除原資料。
- 修正後 `test_telegram_feedback.py`：17 項 passed。
- 修正後 `test_semantic_revisions.py`：7 項 passed，涵蓋 revision/history、不覆寫、重送、衝突 rollback、17→18、SQLite backup/reopen、較新 schema 拒絕降版、UTC/monotonic 分項與無回應不造時間。
- `test_web_event_review.py`：5 項 passed；`test_telemetry_help.py`：2 項 passed。
- 本輪相關 Python Ruff、JavaScript `node --check` 及 compileall passed；完整套件修正後重跑仍待執行，不把局部複測當成全量成功。

## 驗收邊界與回滾

本輪未建置發布包、未改正式 Host、未外送家庭影像、未操作真實 Telegram callback/PTZ。新候選歷史的瀏覽器視覺與 screen reader NOT_VERIFIED。Schema 18 尚未正式部署；部署前應以 SQLite backup API 保留原 DB 一致快照。降版須停止 Host 後恢復相容的完整備份，不宣稱較舊程式能自動保留新增欄位／歷史。新程式會拒絕未知較新 schema。

完整規格的未完成項見 `V4_REPO_AUDIT.md`；測試數量不是 Provider／家庭辨識／通知時間 SLA 的證據。

## 同日後續：Provider 本機 HTTP 與完整回歸

- `.venv\Scripts\python.exe -m unittest discover -s tests -q`：647 項、150.708 秒、OK，完整回歸已確認前述 migration 順序修正。這次原始終端完成結果取代「完整重跑待執行」的目前狀態；歷史失敗保留作為原因紀錄。
- `test_vision_transport.py` 最後局部執行：8 項 passed；真實 loopback HTTP server＋本次 Python helper，不使用真實供應商或家庭影像。
- `.venv\Scripts\python.exe -m ruff check src tools tests`：All checks passed。
- `.venv\Scripts\python.exe -m compileall -q src tools tests`：exit 0。
- 曾發現極簡 Windows environment 使空白 Python probe 不結束；保留必要系統路徑變數後完成。秘密不放 child env／command line；不依程序名稱批次終止。
- 正式 Host、真實 Provider 分析、多影格 C+、Telegram 本版 E2E、手機/鍵盤/screen reader 全站驗收仍 NOT_VERIFIED／未完成，不用全量綠燈替代。

## 同日後續：多時點、逐軌跡候選

- 完整 `unittest discover -s tests -q`：654 項、158.809 秒、OK。
- `test_semantic_storyboard.py`：5 項 passed；真實 JPEG 拼貼、兩軌跡映射、粗略時間、未知 alias/index/精準時間拒絕、重複／錯誤影格／混合 epoch 拒絕及有界快取。
- `test_vision_transport.py`：9 項 passed；新增兩條軌跡經真實 loopback HTTP 的 request → 驗證候選 → 保留 evidence，沒有外送家庭影像。
- `test_v02_host_analyzer.py`：10 項 passed；新增 opt-in 快取與既有 crop 重用，4 張影格只有 4 次追蹤器 update，不新增第二推論。
- `test_event_semantics.py`：7 項 passed；候選 caption 改「剛剛可能在」，保留原影片送出與有界等待。
- 全量 Ruff／compileall、event-review.js 語法檢查 passed。候選證據的新前端顯示尚未瀏覽器實測；不能用模板／JS checks 當視覺驗收。
- 本轮只證明本機接線與結構驗證；物理 PTZ view epoch／隱私遮罩／完整雲端設定與真實 Provider/Telegram 辨識品質仍未完成。

## 2026-10-15：安全設定、請求預算與冷卻

- 完整 `.venv\Scripts\python.exe -m unittest discover -s tests -q`：671項、160.849秒、OK。
- `test_vision_settings.py`：8項passed；含實際Windows DPAPI暫存資料往返、auth/CSRF、Key不回填、備份排除、更換服務重新同意、停用不外送、schema1設定相容及設定保存不解除401封鎖。
- `test_vision_request_guard.py`：8項passed；UTC每日上限／失敗保留、冪等／重啟、lease過期、刪影片不抹預算、兩個SQLite連線同時只有一個放行、429冷卻單次試探及Key修正解封。
- `test_vision_transport.py`：10項passed；新增實際HTTP429的安全Retry-After解析及無效header不洩漏。
- 全量Ruff、compileall與event-review.js／vision-settings.js語法檢查passed。
- 隔離設定頁此前已實際保存grace7並重載確認、enabled/consent=false；390px無水平溢出、Enter/Esc及焦點、console errors=[]。每日上限新增欄位尚未重做瀏覽器操作驗收。18943實際listener數再次確認為0。
- 未建置、部署正式Host或送出家庭影像；真實外部Provider／本版Telegram E2E／全站UX仍not_checked／未完成。
## 2026-10-15 schema23／按鈕版本防重複

- 完整回歸：701 tests、169.129秒、OK（exit0），輸出 `reports/v43-callback-revision-unittest.txt`。
- 局部：逐軌跡19、Telegram poller19、Dashboard review8項passed；涵蓋真實SQLite雙連線競爭、舊按鈕不更動、同update重送、schema22欄位遷移重跑、64-byte限制。不是實際Telegram連線。
- 全量執行期間另補強重複識別查詢需檢查所有相符歷史，及stale按鈕更新失敗不能宣稱「已保存」；補強後逐軌跡19項重跑passed。此局部結果與完整執行分別記錄，不宣稱完整套件已重新載入這兩處最後補強。
- 全量Ruff、compileall passed；未建置、發布、安裝、重啟正式Host或操作真實Bot。
- V4.3整體仍未完成；三步回報的實際Bot點擊、順序導航及影像框號對照仍需驗收。

# 2026-10-15 原影片逐軌跡入口追加驗證

本輪 `test_telegram_feedback.py`：18 tests、3.614秒、OK；`test_track_tag_feedback.py`：10 tests、OK；`test_web_event_review.py`：6 tests、OK；`test_notification_receipts.py`：3 tests、OK。指定修改檔案 Ruff 與 compileall passed。

兩軌跡測試改從原生影片的 media receipt 開啟回報，而非預先塞入 behavior receipt；驗證鍵盤、同recipient/message授權、身分與多選確認落庫。另測同track有多筆相交事件時不猜選、不同來源／時間拒絕、錯誤收件者／訊息拒絕與重複綁定冪等。仍使用 FakeTelegram，不是真實發送或點擊。

前輪 session 55337 本輪查詢為 unknown handle，沒有可確認的最終輸出，不計為通過；本輪 session 74248 完整回歸：677 tests、151.404秒、OK（exit 0）。全量 `python -m ruff check src tools tests`、`python -m compileall -q src tools tests` passed。不重啟或更新正式 Host。
