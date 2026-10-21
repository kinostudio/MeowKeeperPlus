# r97 更新發布進度

## 已完成

- 身分候選／人工修訂／明確學習同意與撤回；參見 AI 身分規格驗收報告，家庭校準仍未通過。
- 首頁／事件／寵物比對介面與原始 AI 證據分別保存。
- 進階設定更新入口：登入／CSRF，官方 GitHub 公開來源、Ed25519 簽章、SHA-256、大小上限、安全 ZIP 解壓、完整發布 manifest 驗證。
- 更新只下載並準備，不自動安裝、不觸發 UAC、不停止目前監控。資料不回傳 GitHub。
- 可攜圖示工具：`Create-Launcher-Icon.cmd`，建立根目錄 `MeowKeeperPlus.lnk`。
- 612 unittest passed，214.876 秒；Ruff／compileall passed。
- 實際 Windows PowerShell 圖示生成驗證：目標 embedded pythonw、工作目錄及大地色 icon 均正確；只在隔離 reports 目錄測試，不啟動正式 Host。
- r97 建置 20,596 檔案：`dist/MeowKeeperPlus-portable-2026-10-14-r97`。
- 公開 repository 已透過使用者登入的瀏覽器建立：`https://github.com/kinostudio/MeowKeeperPlus`。

## 更新邊界

自動停止／切换／回退、資料庫 migration 失敗還原、無人值守更新與私人下載授權尚未實作，不宣稱全自動更新。保留舊包及既有完整資料備份，使用者手動安裝。

正式 Host 未升级、未重啟；家庭辨識、真實 Telegram 點擊、24H 及新更新頁視覺驗收尚未完成。

私人簽章金鑰只在 Git／發布排除的 data 目錄；只將公鑰放進包。公開附件必須是完整驗證後的 ZIP 與簽署 manifest，不是本機工作資料夾。
