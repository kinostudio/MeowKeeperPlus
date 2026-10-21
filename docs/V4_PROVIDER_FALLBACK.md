# V4.3 Provider 與回退（施工中）

## 已接入的 adapter／傳輸

`integrations/vision/openai_compatible.py` 採圖片 Chat Completions 格式；model_id 由設定提供，不假设所有模型支援圖片或 JSON。capabilities 目前只宣告 image，JSON object 由明確能力設定啟用；不宣告 video、schema 或 Token 計數。

圖片部分與 JSON object 格式依官方 [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) 核對；這不是真實付費 API 已接通證據。

預設 enabled=false、upload_consent=false，不呼叫外部。此 adapter 尚未接入正式 Host／設定 UI；Key 以 callable 僅在實際請求時讀取，未建立 Key 儲存與同意版本管理。不能把此布林 gate 當完整隱私授權介面。

## 真正的 transport 截止

stdlib HTTP 在本次專屬 Python helper 執行；秘密與圖片只透過 stdin，不在 command line、環境變數或檔案。父程序 communicate deadline 包含 DNS/TLS/response，逾時停止持有的程序 handle；Windows venv 子程序依血緣、腳本完整路徑、psutil PID 重用驗證後停止，不依名稱批次停止。

必要 Windows 路徑變數保留，API Key 等環境不繼承；原精簡環境在實測中連 Python print probe 也無法結束，補回系統路徑後正常。helper `-I`、shell=False、無 console、stderr 丟棄、不使用自動 proxy、不跟隨 redirect。remote endpoint 僅 HTTPS；HTTP 僅 loopback，禁止網址帳密/query/fragment。回覆上限 1 MB、輸入上限 8 MB。

## 分類與目前回退

401/403→auth_error；429→rate_limited；5xx/DNS→unavailable；逾時→timeout；格式／redirect→bad_response。只保留安全分類與整數 HTTP status；原始錯誤文字不傳到通知／日誌。EventSemanticService 接受分類 terminal result，通知 Future 結束即可沿原影片／本機 caption，不等滿 grace。

本輪新增 8 項本機 HTTP 測試 passed：子程序真實 HTTP、關閉／未同意無請求、deadline、錯誤碼、redirect 拒絕、格式錯誤、Key 不在參數或環境、單張不能變時序動作。未傳家庭影像或呼叫真實 Provider。

## 尚未完成的完整契約

- 單張／未映射 multi-track 仍 insufficient_evidence，不以 prompt 當安全保證。現已接 numbered storyboard 與 evidence validator，兩軌跡 loopback HTTP候選測試 passed；物理PTZ／區域／隱私與家庭辨識仍未验收。
- Dashboard設定／獨立外送同意與DPAPI Key已接、尚未部署；其他原生protocol adapters、health check及capabilities registry待完成。
- schema19的請求保留支援每日budget、429 Retry-After、circuit breaker、45秒lease和input hash冪等；請求/狀態查詢時回收過期工作與90天前保留資料。沒有新工作時的全背景掃除與持久化queued job恢復仍待完成。目前不自動重試請求，避免重複費用。
- 0/5/10 秒 fake-clock、全階段耗時、真實 Provider/Telegram E2E 尚未完成，不能宣稱完整故障矩陣或整體 V4.3 通過。
