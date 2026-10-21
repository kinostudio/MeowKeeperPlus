# Telegram 貓咪正誤回饋閉環

2026-10-12 更新：一般逐貓摘要、對照圖片與按鈕整合為同一則；過長或圖片失敗才退回文字，回執綁真正帶按鈕的訊息。詳見 [r89 通知與位置圖](R89_NOTIFICATION_POSITION_RELEASE.md)。下列歷史「下一則摘要」流程已被此更新取代；真實 Telegram E2E 狀態仍需本輪外部驗收。

狀態：source_and_local_feedback_loop_verified／v02_track_digest_and_feedback_local_tests_passed／real_telegram_callback_not_checked。

## 目前程式證據

- `core/notifications/telegram.py`：訊息、JPEG、MP4 可附 Inline Keyboard，支援 getWebhookInfo、getUpdates、answerCallbackQuery。
- `core/notifications/telegram_feedback.py`：單一背景 long-poll worker；先檢查 webhook，不刪除既有 webhook；只接受白名單私訊中 sender ID 等於 private chat ID 的回覆；HTTP 400／409 衝突時停止輪詢。
- `core/media/event_clip.py`：detector `cat_detected` 的影片及組片失敗後 JPEG 截圖都附「是貓咪／不是貓咪」按鈕；模擬事件不附可校正按鈕。
- `storage/database.py`：目前 schema version 11；媒體事件與 V0.2 行為軌跡分別保存送達回執、每位回覆者的最新標籤與不可覆寫歷史，callback／update offset 原子去重。V0.2 標註維度含貓咪正誤、個體、主要行為與動作；migration 保留舊標註資料。
- `GET /api/v1/analytics/cat-feedback` 與 AI 設定頁呈現已回饋告警的樣本、爭議、模型分數分桶及已審核 precision。
- `tests/test_telegram_feedback.py` 以本機假 Telegram 完整驗證事件 MP4／JPEG 按鈕、成功收件回條、私訊授權、兩種標籤、同一人修正標籤、資料庫標註與統計；這是自動整合測試，不等同真實 Telegram E2E。
- 本輪補正無效 callback 的 in-memory／SQLite offset 同步，避免同一筆壞 update 在長輪詢中反覆被取回。
- 統計提供整體及模型分數分桶的 Wilson 95% 區間，將小樣本不確定度呈現出來；這不是模型校準，也不會自動修改原始分數、門檻或權重。
- 前一輪回饋變更曾通過 270 unittest、Ruff、compileall、`ai.js` Node 語法檢查，r27 可攜包檢核通過：18,680 檔、私人資料檢查 false。r27 早於本輪 Command Router／schema 7 變更，不代表含本輪變更的包。
- 本輪新增 command router、PrivacyService fail-closed 邊界與 reset callback 原子 offset 提交；全套 287 unittest 通過，Ruff 與 compileall 通過。這些是原始碼／測試層證據；發布包另由其內附 verifier 驗證，安裝／執行狀態另行核對。
- 先前檢查顯示 ProgramData manifest 指向 r14（2026-09-24、18,664 檔）且缺少回饋接收器；本輪未重新盤點目前安裝 Host，因此標為 `not_checked`。無論 `/healthz` 是否可用，都不能單獨證明正在跑哪個 package。
- 真實授權私人 Chat 的兩種按鈕、Telegram callback 成功提示、正式 Host SQLite 標註仍需安裝／啟動最新可攜包並實際點擊；本輪沒有送出外部 Telegram 訊息或重啟正式 Host。

## 最小實作範圍與資料流

既有事件 → 有界 outbound queue → JPEG／MP4＋Inline Keyboard →
單一背景 getUpdates long polling worker → 權限及回條驗證 → SQLite 回饋 → answerCallbackQuery。

- 使用一個 Bot，不開公開 webhook、不改 Dashboard 登入、不新增攝影機 consumer。
- 明確啟用才輪詢；disabled 時不得連線或傳送，停止時釋放 worker。確認沒有既有 webhook／其他 poller，衝突時安全停止接收，不能偷偷 deleteWebhook。
- 按鈕為「✅ 是貓咪」「❌ 不是貓咪」；允許稍後更正。意義為：事件證據中是否可確認有貓，不代表寵物身分或行為正確，也不代表每個框都正確。
- 以同一 event_id 連結 JPEG／MP4；按鈕 payload 不含秘密，遵守 Telegram callback_data 上限。
- 以 Chat ID 與發送回條綁定事件／message_id，另驗證操作者 user ID 的回饋權限；群組不能只靠群組 Chat ID 接受所有人的標註。
- 只有已選取的私人 Chat ID 可以回覆，且必須與實際 Telegram receipt 的 chat hash／message_id 相符；群組、未授權對象、模擬事件、過期或無回條事件會拒絕。此版 feedback 權限沿用啟用中的私人通知對象，不另設第二份使用者清單。
- 重複 update／callback 冪等；保存處理 offset 防止重啟重播。SQLite 暫鎖時有限重試，不得先提交 offset 而遺失回饋。
- Telegram、SQLite 問題不得阻塞 ingest；worker 有逾時／有限退避／有界排程，快速安全確認 callback 後再完成較慢工作。
- 最終顯示「已記錄：是貓咪／不是貓咪」或明確錯誤，不能只消除 Telegram loading spinner。

## 本地資料契約（需 migration，不改寫舊表語意）

- 新媒體回饋表：event_id、reviewer_ref、label(cat/not_cat)、callback/update 識別、created_at、updated_at。
- 保存每人最新選擇及可稽核更正歷史；同一人的多次點擊不算多份樣本。
- 多人相反選擇標記 disputed，排除自動校正；不讓最後一人覆蓋其他人的證據。
- 保留觸發 origin(detector/simulated/test)、model SHA/version、原始 confidence、當時 threshold、source profile、事件時間。
- Token、完整 Chat/User ID、原始 update body 不進一般日誌或報告；身份索引用本地安全引用，資料庫仍屬私人資料。
- 原始模型分數不可因使用者按鈕而改寫；人工標籤與校正版本另存。
- 正／負標註不刪影片、不再發重複警報；依既有保存政策保存。

## 信心指數校正界線

1. 先提供已回饋件數、未回饋件數、爭議件數及已審核告警中的 cat 比例；清楚標示一致樣本量，並用 Wilson 95% 區間顯示小樣本的不確定度。
2. 只審核已推送事件會有選樣偏差，只能估計警報 precision；不能由此推導 recall、整體 accuracy 或所有影格的校準機率。
3. 不把 YOLO 0.72 寫成「72% 真實正確率」；顯示「模型分數」，沒有校正資料時標為未校正。
4. 真正門檻評估需額外人工標註未告警影像／漏報，按日期／事件分割獨立驗證集，避免相鄰影格洩漏；不同來源與模型分開統計。
5. 資料充足後產生候選門檻、樣本量及不確定區間；使用者批准後才版本化套用，可回退。少量回饋不立即改門檻、不自動重新訓練。

## 驗收條件

- cat-only 真實事件 JPEG／MP4 均含兩個按鈕；本機整合測試已驗證按鈕 payload、送達回條及兩種標籤落庫。授權使用者在 Telegram 實際點擊、收到成功提示並寫入正式 Host SQLite：`not_checked`。
- 同一人更正、群組／未授權聊天拒絕、錯誤 message_id、模擬事件不提供可校正按鈕及偏移量前進均有測試。多使用者衝突與正式程序重啟後 offset 的真實 Telegram 行為仍需外部 E2E。
- Telegram disabled 外部呼叫 0；網路失敗／SQLite 暫鎖不影響共享預覽及環形快取；不存在第二個 ingest／poller。
- 紀錄 API 成功與 message_id 不等於人的標註；真實回饋至少一筆 cat、一筆 not_cat 才通過操作閉環。
- 負面影像漏報／召回率驗收另列；沒有資料時保留 not_checked，不虛構信心改善百分比。
- 本地回饋閉環自動整合測試已通過；正式安裝／啟動新版、真實 Telegram 按鈕點擊及家庭實拍標註仍未驗收。

## V0.2 逐貓摘要與逐軌跡回饋驗收

- core/events/v02_pipeline.py 將同一分析批次可見的多條軌跡整理成一則繁體中文摘要；每列同時顯示目前可確認的個體／未知身分、主要行為與動作狀態，且不輸出內部 track ID。
- 摘要以既有有界 Telegram dispatcher 排程，逐收件者遵守通知開關／靜音；只有該收件者啟用 /feedback on 時才附逐軌跡按鈕。
- 每條按鈕指向各自的 SQLite behavior_events.event_id。送達後保存 recipient hash + message ID 回執；按鈕先開啟單軌跡選單，回饋再次綁定該事件與該送達訊息。
- 單軌跡回饋包含是否為貓、個體、主要行為及獨立動作（walking/running/jumping 等）；先確認是貓才可寫個體、行為或動作。標為非貓會清除同一回覆者的目前細項值，稽核歷史不刪。
- 雙貓摘要、每隻軌跡的個體／行為／動作分開保存、收件者 opt-in、真實送達回執、舊 schema migration 保留資料：tests/test_telegram_feedback.py 本機自動測試已通過。
- 摘要是在行為事件完成／切分後彙整，不是每影格推播；用餐等事件可能經過合併窗口才完成。Telegram 真實 API、使用者實際點擊、正式 Host 安裝／SQLite 寫入及家庭準確率仍為 not_checked。confidence 僅是未校正模型分數，人工回饋不會即時重訓。
