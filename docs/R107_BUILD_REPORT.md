# r107 建置與發布報告

日期：2026-10-18

## 改動

r107 首頁硬體卡加入即時採樣倒數圓環、CPU／RAM／GPU 垂直數字過渡、使用率環圈及 CPU／GPU 溫度指針。動畫只訂閱既有狀態快照；沒有增加感測程序、API 輪詢或採樣頻率。感測資料缺失時明確顯示「尚未感測」，不以 0 代替；尊重減少動態效果設定。

## 程式驗收

- 完整 unittest：877 項通過，2 項因隔離環境缺少實機媒體條件略過。
- Ruff、compileall：通過。
- Edge 合成 telemetry 驗收：數值／圓環／指針過渡、採樣時間／倒數及感測值缺失狀態通過。
- 上述 Edge 測試是合成資料，不是正式 Host 畫面或真實硬體溫度驗收。

## 建置與發布

- Windows 可攜包目錄：`dist/release-r107/MeowKeeperPlus-portable-2026-10-18-r107`。
- GitHub Release 已發布：https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r107（3 個 ZIP 分卷、下載／驗證腳本、簽章更新清單）。
- 建置包含 DINOv2 實驗模型、CPU temperature helper 與已驗證 PTZ backend；正式安裝版／Host 未升級，沒有觸發 UAC。
- 發布包不會自動安裝／重啟正式 Host。取得資產及核對簽章狀態，請以官方 GitHub Release 頁面和更新清單為準。
