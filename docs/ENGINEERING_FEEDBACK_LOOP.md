# 工程故障回饋閉環與發布防退化

2026-10-17。這是repository新增機制，尚未進新發布包或安裝到正式Host。

## 1. 現有能力與原先缺口

原先已有LESSONS、單元測試、包雜湊、所屬程序停止、程序已退出的競態處理、舊App封存，以及複製／校驗失敗時恢復舊App。這些是局部防護，不是完整工程閉環：沒有統一故障台帳、強制針對歷史故障測試的建置門檻與安全結果摘要，仍可能以不同runtime或漏測版本重犯。

「遞迴自我改進」在此指**工程流程反覆消除失敗**，不指在使用者電腦自動生成／改寫程式碼、關閉安全限制、修改台帳來取得綠燈，或自動重訓AI模型。這些都不是目前功能，也不應作為正式產品預設。

## 2. 已實裝的閉環

1. **觀測**：安裝器記錄目前階段；已有Data目錄且非WhatIf時，結束／失敗保存白名單摘要至 `Data/install-history/attempt-<隨機識別碼>.json`。只有schema、UTC、r版本或unknown、狀態、階段與分類碼；不保存例外原文、帳號、路徑、命令列、Token或攝影機資訊。摘要寫入失敗不覆蓋原始安裝結果。摘要不自動上傳，亦不自動從私人Data加入發布包。
2. **分類與重現**：維護者將故障對應到 `configs/release-regressions.json` 的穩定ID。每項必須有明確unittest方法，不能只留文字「已修復」。未知故障由遮罩摘要與本機診斷定位，不推測根因。
3. **修正與回歸**：先用隔離／注入故障重現，再修最小範圍。記錄LESSONS、測試與限制；不觸及其他軟體的程序、Data或憑證。
4. **建置阻擋**：`build_portable()`在建立staging／覆蓋原包前執行 `tools/verify_regression_gate.py`。每次重新執行，不讀前次綠燈快取；失敗、skip、預期失敗、載入錯誤、缺結果或180秒逾時都拒絕。`--dry-run`只列計畫，不宣稱通過門檻。
5. **證據綁定**：門檻前後雜湊第一方src/tools/scripts/tests/configs；測試期間或包複製期間程式改變即失效。結果與故障台帳雜湊放入 `release_regression_gate.json` 及build_manifest，檔案本身也納入完整包校驗。
6. **發布再檢查**：`prepare_release.py`在產生ZIP／讀取簽署私鑰前，比對包內結果與build_manifest，並核對目前台帳及程式指紋。舊包沒有這份證據時需重新建置，不准補寫假passed。這不取代既有逐檔雜湊或簽章。
7. **實機回圈**：由使用者安裝新包；核對installed/running版本，再走原故障真實流程。通過才能將該情境關閉。真實硬體／外部服務待驗時維持not_checked，必要時保留experimental。

目前台帳共28類／93項方法登記（90個不重複方法），新增受限測溫工作／一般Host帳號分離、過期與停止請求，以及CPU驅動同意、非阻塞GPU採樣、雙採樣錯位、歷史降階、資料根目錄、安全事件路徑及回復journal保留。SHA-256、SQLite備份、條件更新與冪等均有回歸；未比對成功不改寫。資料目錄門檻驗證Tray依Host回報、圖形入口讀Core參數，不讓不同入口猜路徑。**這不是所有已知／未知缺陷的完整證明**；每輪新增故障須擴充。ROOT 的 CMD／PS1 啟動器與helper C#來源亦納入程式指紋；helper原始碼雜湊需與stage manifest一致。

## 3. 不應再次發生的具體含義

可重現且測試覆蓋的同一條件，應在建置時被攔下。不同防毒、ACL、磁碟／網路狀態或新runtime仍可能產生新故障；工程承諾是安全停止、保留資料、可定位與可恢復，不是零故障保證。

安裝分類碼：`INSTALL-PREFLIGHT/STOP/ARCHIVE/COPY/VERIFY/REGISTER/START/HEALTH`；成功為 `INSTALL-OK`。摘要只在Data已存在後盡力保存；UAC取消、初始參數錯誤／Data尚不存在等早期失敗可能無摘要，不能推論沒有故障。無限歷史保留尚未提供，需另納容量政策。

目前回復涵蓋**新版 App 完整性校驗前**的舊 App 復位，並以獨立 journal 包住舊 `App\data`／根目錄設定檔的移入：遷移衝突不移動任何資料；校驗前後續失敗會嘗試回復並記錄結果。它不代表資料庫 schema migration 或已改排程可以完整回退；校驗通過後的註冊／啟動／健康失敗需保留舊 App 備份與 Data 供復原，不可假稱整筆安裝交易已回滾。完整程式＋Data＋排程原子回退仍是後續工作。

## 4. 執行方式與證據

```powershell
.venv\Scripts\python.exe tools\verify_regression_gate.py
.venv\Scripts\python.exe -m unittest tests.test_release_regression_gate tests.test_installer_legacy_tray -v
```

門檻測試不啟動安裝器、不要求UAC、不終止真實程序、不發Telegram或雲端圖片。PowerShell測試只抽取函式，在臨時目錄或替身程序盤點下執行。測試摘要只列ID，不原樣輸出可能包含秘密的stderr。

純文件修改不必重跑產品全測；變更安裝、建置或跨模組流程則仍需全量unittest／ruff／compileall，以及實際Windows shell隔離執行。包內與正式機驗收不能由此門檻替代。

## 5. 下一階段仍需補足

- 安裝階段故障注入：archive、copy、verify、register、start、health，檢查舊App／Data／排程復原；目前不能宣稱全部交易回退完成。
- 安裝摘要在Dashboard的遮罩檢視／匯出與容量管理；現在只有本機JSON，未接自動報修。
- 專用Windows發行CI及包內啟動／WS解碼smoke，不能用開發venv綠燈替代。
- [產品規格](PRODUCTIZATION_SPEC.md)中的PTZ真實驗收、ID-01～03、WebRTC／共享轉碼及家庭長測仍各有獨立工作，不能靠新增此文件自動關閉。
