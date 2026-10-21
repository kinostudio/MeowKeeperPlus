# V4.3 事件候選與版本模型（施工中）

## 已實作的資料邊界

schema 18 新增 `event_semantic_revisions`：同一 media event、逐 event revision、唯一 job UUID、狀態、安全 candidate JSON、UTC 建立時間。UPDATE trigger 阻止覆寫；事件保留政策刪除主事件時仍允許 cascade 清除，並非永久保留家庭資料。

`record_event_semantics` 在同一 transaction 追加 revision 並更新相容舊表投影。相同 job／event／payload 重送視為冪等；不同內容占用同 job 拒絕並 rollback。不將 AI 結果寫入寵物身分或人工學習表。

17→18 遷移只保留已有最後候選為 legacy 第一版，不補造舊工作識別／時間。可重跑，後續有 revision 不再複製投影。SQLite backup/reopen 已有局部測試；完整加密 ZIP 恢復及正式降版 NOT_VERIFIED，不宣稱跨版回滾無損。

遇到較新 schema 版本會拒絕初始化，不靜默覆寫 version。候選資料遷移排在舊行為表結構 migration 之後，避免 transaction 重疊；相關舊 Telegram migration 已複測。

## 耗時契約

worker 產生 job UUID，記錄 queued/start/response/finished UTC；queue_ms、api_response_ms、validation_ms 分開以 monotonic 差值量測。失敗在 HTTP 返回前沒有 response_received_at/api_response_ms，不以假的 0 填補。白名單欄位禁止任意 response/payload/secret 存入 telemetry。

本次 started_at 是 worker 開始處理，不代表獨立外部服務已接收；provider 契約仍需真正 transport timeout。未量測 preparation、media_sendable、grace、Telegram accepted，不能產生總耗時／瓶頸結論。

## 前端

既有認證 event review API 附帶 semantic_history；事件回報顯示歷次候選、白話狀態與已知三項耗時。人工歷史與 AI 歷史分區；候選不默默預選。

## 尚未完成

Tag taxonomy 維度／version、證據索引、certainty/time_quality、job 持久化租期與冪等 input key、PTZ epoch、provider/model/config provenance、完整時間軸與統計仍待建。現有 walking 等 TAG 只是受控候選，不是家庭行為準確度驗收。

同日後續已接 storyboard alias／evidence index／certainty/coarse time：`TagEvidence` 綁定本次輸入軌跡、候選 code 及至少兩張影格，禁止模型發明精準片段。結果只有相對觸發的觀察點，不等於影片 PTS；詳見 `V4_STORYBOARD_INPUT.md`。版本化 taxonomy、物理 PTZ 與完整 provenance 仍待建。
