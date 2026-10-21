# r98 公開發布驗收

## 程式與包

- 包：`dist/MeowKeeperPlus-portable-2026-10-14-r98`，20,598 檔案。
- 身分回饋修正、明確學習同意、共享 DINO 模型與事件證據；家庭校準仍 experimental。
- 更新：只檢查／下載／驗證／準備，登入與 CSRF 保護；固定公開 GitHub 來源，Ed25519 信任根；簽署分卷与整包大小／SHA-256。
- r97 單檔 ZIP 超過 GitHub 2 GiB 上限，未發布；r98 使用 1 GiB 分卷，更新工具自动合併。
- 桌面快捷入口由 `Create-Launcher-Icon.cmd` 產生，目標內附 Python、正確工作目錄及大地色 icon；已用隔離 Windows COM 實測。
- 完整 613 tests passed，206.159 秒；Ruff／compileall passed。分卷安全測試7項；不等同真實下載更新 E2E。
- 包內 `updates.py` hash 與本輪原始碼一致。
- 包內程式／設定／文件掃描：指定 Bot Token、小米完整帳號來源、私鑰格式 0 命中；不宣稱涵蓋所有秘密類型。
- 嚴格完整 manifest 驗證由 `prepare_release.py` 在壓縮前執行；不放寬私人 Data 邊界。

## 發布

公開 repo 已透過使用者登入的瀏覽器建立 `https://github.com/kinostudio/MeowKeeperPlus`。GitHub CLI 當前帳號沒有 kinostudio 建立／上傳權限，沒有把其 Token 放入應用；瀏覽器使用 kinostudio 既有登入。

實際公開 Release：`https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r98`。公開 API 確認 draft=false、五個附件均 uploaded；三卷、簽章 manifest 與首次下載腳本的遠端 SHA-256 全部與本機相符。實際從 latest 下載 manifest，Ed25519 驗證 passed。未在另一台電腦重新下載整包／安裝，不能替代異地 E2E。

發布照片：`reports/r98-github-published.png`。首次下載腳本：`scripts/Download-MeowKeeperPlus-r98.ps1`，固定完整 ZIP 校驗碼，不自動安裝。

這次第二卷一度只建立附件占位、後端狀態為 upload failed；第三卷也曾失敗重試。附件名稱出現不等於 uploaded。重新載入草稿確認實際大小／失敗狀態，移除失敗占位並只重傳該卷；不得發布缺卷的版本。第一個 r97 超過單檔上限未發布。

附件只發乾淨程式分卷与簽署 manifest，不發工作目錄、資料備份、報告、私人簽章金鑰或 Xiaomi Session。最終附件／網址／上傳狀態以 GitHub Release 實際頁面為準，建立 repo 不等於附件已上傳。

## 明確未完成

- 自動原地切換、安裝失敗自動回退、資料庫回退及無人值守升級。
- 新更新頁視覺驗收、另一台電腦真實下載解壓及更新。
- 家庭身分校準、雙軌跡衝突對稱撤除、已確認回饋裁切縮圖與 gallery rollback。
- 本輪真實 Telegram、C701 筆電及24H驗收。

正式 Host 未重啟／升級。本包可供使用者自行驗收，不描述為所有規格已驗收完成；第三方授權與商用條件另有要求。

## 建議模式

固定主監控電腦：SYSTEM 安裝模式，未登入 Windows 仍可監控；資料獨立 ProgramData Data。異地首次測試：可攜模式，完整資料夾開啟即用；移動後重建圖示。穩定後異地監控機也可改 SYSTEM 安裝模式。
