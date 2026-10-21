# 目前目標

## 共同原始碼

- GitHub 為 Codex／Argon 的共同來源；以工作分支、完整 commit SHA 及乾淨 clone 驗證。
- 保留既有影音、事件、通知與身分回報功能，補齊來源檔、測試與圖形入口的交付一致性。
- 交接包含完整來源／測試清單、每檔 SHA-256、基準與完成 commit，以及同一份程式碼的測試結果。
- 公開內容只含程式與一般說明；實機診斷及私人交接封存於本機 Data。

## 驗證順序

1. 設定保存、學習特徵保護、事件回報與來源切換的隔離回歸。
2. 完整 unittest、Ruff、compileall 與相關前端測試。
3. Windows 啟動器與發布包的隔離啟動驗證。
4. 使用者另行安裝後，核對實際執行版本及攝影機、PTZ、Telegram、GPU／CPU 感測等外部功能。

前三項不能冒充第四項已完成。身分與行為候選保持 experimental；沒有新實機證據時不改為正式支援。

整合結果見 [整合紀錄](SOURCE_INTEGRATION.md)，功能目標見 [產品規格](PRODUCTIZATION_SPEC.md)。
