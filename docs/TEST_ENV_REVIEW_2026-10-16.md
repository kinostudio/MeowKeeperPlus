# 測試環境覆核：不安裝、不升級正式 Host

日期：2026-10-16（Asia/Taipei）。本輪範圍為預覽／AI 八項要求及上一輪修正的接線與邊界條件；不是宣稱所有歷史規格完成。

## 新找到並修正的 bug

1. **休眠／快速模式切換的非同步競態**：舊 metadata、JPEG body 或解碼完成回呼，可能在停止後繼續工作，甚至產生第二個輪詢迴圈。加入 generation 與每份請求獨立 AbortController，舊回應不能更動新播放工作。實際執行 JS 測試覆蓋晚回 metadata、晚回 JPEG、休眠後恢復同模式及唯一輪詢。
2. **跨來源 AI 框殘留**：舊 overlay 時間未超過 2 秒時，原先可能套在新來源。每次監控啟動建立不含秘密的 epoch，啟停清空 overlay；Web façade 只允許同 epoch 資料。單元測試確認舊 epoch 拒絕、新 epoch 接受。
3. **休眠仍有 AI 繪圖計時器**：原先清空圖層後每 250 ms 仍重畫空圖層。現在只在有效資料時啟動，休眠／隱藏／過期均停止計時器。JS 測試另驗座標比例、無效框拒絕及標籤只用 textContent。
4. **降階歷史被恢復狀態覆寫**：恢復可用時，原先可能用目前較低耗時更新舊 trigger 的歷史耗時。只在新觸發物件出現時補寫當時測量值。
5. **匯出失敗卡住全域鎖**：暫存目錄建立失敗原先發生在 try/finally 外，後續可能永遠回 409。移入保護區並加失敗回歸；壞的 worker JSON 轉為安全 503，清理暫存並釋放鎖，不回傳 stderr／路徑。測試連續重試仍可進入工作而非卡鎖。

## 驗收矩陣

| 要求 | 測試環境可驗項目 | 結果與限制 |
| --- | --- | --- |
| 日誌分類／降噪 | 固定類別、次數彙整、不輸出任意錯誤文字 | passed；真實外部 ACL／防毒占用不能只靠模擬定因 |
| 影片實際尺寸 | MSE HUD 用 videoWidth/videoHeight；JPEG 與原碼分開 | 程式回歸 passed；上一輪實際 MSE 1080p20 通過，本輪未重複冒稱 C701 4K 實機 |
| GPU 降階 | 溫度／負載／耗時、平均取樣、手動重試、歷史觸發 | 相關 policy／analyzer 測試 passed；不等於 MX250 長時間效能驗收 |
| JPEG 尺寸上限 | 設定與驗證、單一 ingest、不同分析尺寸 | 現有相關測試 passed；1920 為 JPEG 軟體限制，不是原碼限制 |
| 身分／YOLO 尺寸 | 各自語意及傳遞、原圖座標 | 相關測試 passed；身分正確率仍 experimental |
| HUD AI 開關 | 元件接線、不同候選、座標、過期／休眠／來源切換 | 自動 JS／Python 回歸 passed；本輪新版實際瀏覽器重驗受工具阻擋 |
| 選用標記匯出 | 登入與 CSRF、有效 MP4、時間窗、原片不變、錯誤復原 | 真實 FFmpeg 與 API 回歸 passed；上一輪合成素材的瀏覽器下載通過，不冒充真實 Telegram 發送 |
| 三模式與單一上游 | 預設、休眠、切換、MSE 緩衝、斷線、登入／來源 guard | 現有 MSE 與新增競態回歸 passed；WebRTC 及共用 H.264 fallback **尚未實作** |

## 本輪執行結果

- `python -m unittest discover -s tests -q`：760 個，205.515 秒，passed。此後的匯出錯誤處理補強及最後修正另跑 targeted，不宣稱完整套件涵蓋最後每一筆修改。
- 最後 `python -m unittest tests.test_annotated_export tests.test_preview_mse tests.test_preview_diagnostics tests.test_adaptive_analyzer -q`：29 個，1.510 秒，passed。
- `python -m ruff check src tools tests`：passed。
- `python -m compileall -q src tools tests`：passed。
- 新增 `reports/isolated_preview_e2e.py`：僅用 tests/assets/cat.jpg 產生本機測試影片、CPU 模型、臨時資料庫與 18947 測試 Host；不連攝影機、不發 Telegram、不碰正式配置。只確認服務可啟動，**不把它算成前端完整 E2E 通過**。

## 真正阻擋點

瀏覽器工具在前往 `http://127.0.0.1:18947/` 時，自動審查因工具用量限制無法完成。動作未執行；這不是網站安全失敗或使用者拒絕。沒有改用其他瀏覽器、原始協定或間接 HTTP 操作繞過同一個瀏覽器驗收。

因此新版逐頁畫面、真實模型到 HUD 的瀏覽器接線、手機寬度、匯出下載與點擊流程，本輪的完整重驗標為 **not_checked（browser review unavailable）**。恢復瀏覽器工具後，從此隔離 fixture 接續，不需要安裝新版本。

測試服務已停止；18947 無 listener，該 fixture PID 的子程序檢查沒有殘留。正式 Host 未安裝、未升級、未重啟。未建置或發布新版本。
