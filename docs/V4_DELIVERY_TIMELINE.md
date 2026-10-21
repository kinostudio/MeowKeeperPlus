# V4.3 通知時序（部分完成）

## 已接線

`EventClipService` 在影片通過大小、相容格式與必要轉碼後，記錄 media_sendable；只有此後才呼叫語意 Future 的有界等待。影片仍沿用 send_video 與 width／height／duration；文案只在重試與多收件者迴圈前決定一次，不為每位收件者重新等待 AI。

保存 UTC 時刻與 monotonic 耗時；沒有資料不補 0。`video_prepare_ms` 從影片工作排入佇列到可發送，包含工作佇列、後錄等待、組片、通知佇列及相容性處理；不等於從攝影機動作真正開始的時間。`validation_transcode_ms` 是上述準備區間的子步驟，不得再相加。`extra_wait_ms` 是本次 Future 實際等待，`wait_budget_ms` 為本次0–10秒設定。保留實測值，不將排程延遲造成的超出數值強制截小。

每次上傳保存開始／完成 UTC、monotonic 耗時、嘗試次數、收件者SHA-256摘要及是否取得有效message_id。Telegram接受不是手機已讀或手錶已播放；失敗只記錄未確認，不保存原始回應。上傳耗時在API返回後、收件回條落庫前結束，避免把SQLite時間誤算為網路上傳。

時序在本次影片所有上傳嘗試結束後才落庫，不在AI grace內寫SQLite；資料保存失敗不重傳已送達影片、不阻塞攝影機或模型迴圈。每筆上傳明細最多256項，超出保留省略數量，不能視為全部記錄或0次。

## 資料與介面

schema22增加 `event_delivery_traces`，沿用media event外鍵；append-only UPDATE守衛、trace_id重送冪等，隨事件保留規則刪除。白名單拒絕未知欄位、原Chat ID、非有限時間、錯誤時區与超過10秒的設定上限。沒有保存caption、Token、Cookie、Header、媒體URL或外部response。

既有登入保護的事件review API回傳同事件最近100筆處理紀錄，順序為時間／插入順序。事件回報頁新增「通知花了多久？」漸進揭露：可發送時刻、AI候選／本機摘要、各步驟讀數及輕量meter，並接入既有36詞條的media_ready／ai_grace／telegram_upload白話說明。舊事件與未完成通知顯示尚無紀錄，不假裝0秒或成功。沒有額外UI輪詢、影音consumer或模型。

## 驗證邊界

可控時鐘＋FakeTelegram局部測試涵蓋轉碼完成前不等AI、0／5／10秒、候選已完成／空候選／服務例外／不合法結果、本機回退、多收件者重試不重新等AI、不重傳已成功者、時序白名單、append-only、schema21→22重跑及登入保護。同一事件的回報入口已接，但不是正式Host或真實Bot效能驗收。

隔離合成H.264影片頁已檢查桌面與390px：頁面寬375px、dialog clientWidth／scrollWidth均320px，沒有水平溢出；白話說明可Enter開啟、Escape關閉且焦點回到原按鈕。瀏覽器error log為空。範例8秒準備／0.4秒等待／0.7秒上傳只是UI合成資料，不能當成實機性能。

仍缺：API與媒體全部階段的共同job追蹤、通知佇列與重試退避單獨計量、JPEG fallback／disabled／muted等完整事件分母、完整瀑布圖、100筆聚合p50／p95與0／5／10秒回放比較、真實Provider／Telegram／Windows長期驗收及5位新手測試。
