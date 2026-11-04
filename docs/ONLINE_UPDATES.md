# 異地下載、驗證與安全套用更新

2026-11-05 新契約：改為簽章檔案差量；先核對基準與本機檔案，再下載變更檔案，基準不符或只有完整包時停止，不自動回退完整下載。未變 runtime／CUDA／FFmpeg／模型從本機重用；仍需本機候選空間及短暫中斷。r116 一次性小工具與驗收邊界見 [差量 hotfix](DELTA_HOTFIX.md)。以下完整套件流程屬先前版本記錄。

2026-11-05：整合候選已停用直接 Git 覆蓋 App 的更新路徑，新增持久 Data 快照／雜湊、切換前復原紀錄及失敗現場保留。正式工作交易與斷電恢復仍需另驗收；詳見 [r117 安全更新](R117_SAFE_HOTFIX.md)。套用時間會受既有事件資料量影響，不能承諾秒級完成。

2026-10-31：更新架構新增「安裝版一次管理員授權、之後本機 Dashboard 明確確認一鍵套用」的候選流程。這是**完整簽章套件切換**，不是對執行中程序做零中斷 binary hot patch；套用時 Host、Tray、攝影機 ingest 與 Dashboard 會短暫停止。實作尚在隔離候選工作區，未建置、未安裝、未操作正式 Host；Task Scheduler/UAC、安裝回滾與真實 Windows 更新交易仍須候選包驗收。此前 `r98` 文件內容屬歷史流程，不代表現行部署狀態。

`tools/prepare_release.py` 簽署前仍要求本版故障回歸證據與包 manifest、目前來源／台帳相符；不使用其他版本測試紀錄放行。詳見 [工程閉環](ENGINEERING_FEEDBACK_LOOP.md)。

發布區：`https://github.com/kinostudio/MeowKeeperPlus/releases`。只發布乾淨完整程式包，不發布工作資料、報告、帳密或私人簽章金鑰。

## 第一版的實際範圍

Dashboard → 進階設定 → 程式更新：

1. 使用者按「檢查新版」；預設不在背景檢查或下載。
2. 取得公開 `update-manifest.json`，用包內固定 Ed25519 公鑰驗證簽章。
3. 按「下載並驗證」：固定官方 GitHub repo／tag／asset，串流下載，檢查大小及 SHA-256。
4. 解壓前拒絕 traversal、Windows 裝置名稱、大小寫重複、symlink 與容量超限；解壓後再執行既有完整 manifest 校驗。
5. 新包位於目前 Data 根的 `updates/prepared-*/app`；原程式和 Data 不被修改。
6. 安裝版是否可一鍵套用，取決於已安裝的固定更新工作是否通過本機契約驗證；可攜版或沒有該工作時，維持顯示人工安裝指令。

下載期間頁面每兩秒讀取一次後端進度：分卷下載顯示已收位元組／總大小、百分比與卷數；分卷合併校驗、完整 ZIP 雜湊、解壓各顯示自己的百分比。最終程式檔案清單驗證會顯示目前階段，因為該驗證器沒有可安全回報的逐檔進度。百分比只根據實際下載／處理的位元組計算；完成後才顯示「更新包已驗證」與安裝命令。整段準備流程不會停止監控。

更新 HTTP 失敗只保存例外類型，不輸出網址回應中的秘密。路由沿用登入與 CSRF。沒有 GitHub Token 放在包內。

## 安裝版一鍵套用候選流程

1. 安裝版安裝／升級時，需由管理員核准一次 UAC，註冊一個無排程觸發器的固定 SYSTEM 工作。工作只執行安裝目錄內的版本化 PowerShell worker；更新按鈕要求登入、CSRF，並限制從主機本機 Dashboard 呼叫。
2. Dashboard 準備完成後顯示更新版本與會短暫中斷的明確確認；使用者按下確認才送出固定工作要求。工作不接受任意命令列、路徑或下載 URL；只讀取固定 Data 更新目錄中的要求。
3. SYSTEM worker 將 ZIP／簽章 envelope 複製到 SYSTEM／Administrators 保護的工作目錄，重新驗證簽章、大小、SHA-256、解壓路徑及完整程式 manifest，再呼叫已驗證包內的安裝器。
4. 安裝器保留舊 App 與遷移 journal，啟動新 Core／Tray 後驗證 Dashboard 健康、安裝 manifest 與排程定義。worker 再核對結果；失敗則嘗試恢復舊 App、舊資料遷移狀態與舊排程。只有回復健康檢查通過才回報已恢復。
5. 更新封存與使用者資料不同。只有安全檢查證明沒有未遷移資料或重新解析點時才清理舊 App；已驗證的下載套件仍保留在 Data 更新快取，不由 SYSTEM worker 遞迴刪除可由使用者寫入的目錄。

這不提供零停機：套用期間監控、錄影／事件切段、Telegram 傳送與 Dashboard 可能有短暫空窗。若工作定義、路徑、簽章或版本不吻合，必須停止並保留舊版，不得退回不受驗證的安裝器執行。

**尚未驗收的邊界：** 目前來源修改未完成正式 Windows Task Scheduler/UAC 實測、更新失敗注入與重開機恢復測試、簽章候選包測試及正式 Host 驗收。安裝器失敗回滾是程式碼路徑，並不等於所有資料庫 schema migration 都可逆；正式發布前需特別驗證資料遷移回滾。可攜版、CurrentUser 安裝仍使用人工命令，不具 SYSTEM 一鍵套用。未發布新版時檢查會明確失敗，不偽裝沒有更新。

## 可攜版

新包解壓到另一個目錄，先保留舊包。先用既有完整加密備份保留資料，再透過 Tray 完整關閉舊版，啟動新包並還原備份。不同資料根不會自動掃描合併，Xiaomi Session 的跨機登入不在備份範圍。

`MeowKeeperPlus.cmd` 同時開啟／沿用 Tray、核心與 Dashboard。`Create-Launcher-Icon.cmd` 在包根建立具有大地色圖示的 `MeowKeeperPlus.lnk`；移動包後重新建立捷徑。建立圖示不用管理員權限，不會啟動監控。

## 安裝版

程式在 `C:\ProgramData\MeowKeeperPlus\App`，持久資料在 `C:\ProgramData\MeowKeeperPlus\Data`。同一乾淨可攜包包含安裝器，使用 `Install-MeowKeeperPlus.cmd -Upgrade` 升級。SYSTEM 模式需使用者核准 UAC；正式更新前備份 Data。

SYSTEM Core 可在尚未登入 Windows 時監控，Tray 在登入後顯示。可攜版沒有這個開機前保證。Credential Manager 依 Windows 帳號隔離；SYSTEM 與一般使用者的憑證不能假定互通。

## 建置與發布者

`tools/prepare_release.py --init-key` 只在首次建立信任根時使用。私人金鑰位於被 Git／建置排除的 `data/release-signing`，需離線保管；`configs/update-public.pem` 公鑰可發布。不可覆蓋金鑰來修復下載驗證失敗。

驗證完整包後執行 `tools/prepare_release.py --package <完整包> --version r98`，產生 ZIP 與簽署的 `dist/update-manifest.json`。ZIP 超過 2 GiB 時使用 1 GiB 分卷；每卷與合併後完整 ZIP 都有簽署清單中的大小及 SHA-256。更新工具自動合併，不改下載來源。完整 ZIP 超過 6 GiB 則拒絕發布。

發布附件只選指定 ZIP 與簽署 manifest，不上傳 `data`、完整工作目錄或資料備份。身分／行為仍 experimental；測試包不表示家庭準確率、24H、C701 或真實 Telegram 完成验收。第三方模型、FFmpeg 與相依套件的授權維持各自要求，不宣稱商用授權已解決。
