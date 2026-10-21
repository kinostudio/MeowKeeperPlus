# V4.3 共用白話說明

## 單一資料源

`src/meowkeeper/app/web/telemetry_help_terms.json` 原文來自使用者提供的 V4.3 JSON，36 個詞條、schema 1、zh-TW。`telemetry_help.py` 驗證五個文字欄位並注入 Jinja；各頁透過 `_term_help.html` 共用，不自行重寫術語定義。

## 第一輪接入

- 事件回報的範圍 legend、趨勢分析的行為統計標題使用 `tag_review`。
- 原生 details/summary 可點擊、Enter/Space 展開；JavaScript Escape 關閉並恢復焦點，點外部關閉。
- 手機底部展開、桌面就地說明；44px 按區、焦點框、純本機資源，沒有輪詢或新增影音 consumer。
- 無 JavaScript 仍可展開文字；模板自動 escaping，不使用 innerHTML。

## 待驗收／待覆蓋

本輪不是全站完成：總覽、AI 設定、身分登錄、未建立的時間軸與 AI 效能指標仍需逐 key 對照。用詞字典不等於 telemetry 已產生；缺數據不能填 0、綠勾或假原因。

瀏覽器觸控／keyboard／screen reader、360/390/1280/1440、長文／對比與真實五種工作情境未在本輪驗收。5 位未參與開發者的使用測試標 NOT_VERIFIED，不以程式測試取代。
