# 總覽即時硬體動畫驗收（2026-10-18）

## 本次變更

- 採樣時間改為倒數圓環；直接追蹤既有 telemetry 快照，不增加 API 輪詢或硬體感測次數。
- CPU、RAM、GPU 數值以垂直數字輪播過渡；使用原有即時狀態事件更新。
- CPU／RAM／GPU 環圈與 CPU／GPU 溫度指針依新數值平滑移動。
- 沒有感測值時顯示「尚未感測」，不以 0 代替；GPU 型號取自 GPU telemetry，不從 AI 運算裝置推定。
- 支援 `prefers-reduced-motion`，分頁隱藏時暫停倒數動畫；不改變 telemetry 的取樣頻率。

## 驗收結果

- 完整單元測試：877 項通過、2 項略過；略過項目需要隔離環境以外的實機媒體條件。
- `ruff check src tools tests`：通過。
- `compileall -q src tools tests`：通過。
- Edge 合成 telemetry 測試：數值更新、圓環／指針改變、最近採樣時間與倒數、缺失 GPU／溫度資料顯示均通過。
- 此瀏覽器驗收使用合成狀態資料；不等於目前正式 Host 的硬體感測實機驗收。

## 發布狀態

建置／簽章／GitHub 發布證據見 [r107建置與發布報告](R107_BUILD_REPORT.md)。發布包不會自動安裝或重啟正式 Host。
