# 異地下載與更新（r98）

2026-10-17建置端補強：`prepare_release.py` 簽署前要求本版故障回歸證據與包manifest、目前來源／台帳相符；舊包若缺少證據需重建，不使用其他版本測試紀錄放行。此為發布端防退化，不會自動升級使用者電腦。複製／校驗失敗時舊App回復已有局部實作；健康檢查、排程與資料schema的完整交易式自動回退仍未完成。詳見 [工程閉環](ENGINEERING_FEEDBACK_LOOP.md)。

發布區：`https://github.com/kinostudio/MeowKeeperPlus/releases`。只發布乾淨完整程式包，不發布工作資料、報告、帳密或私人簽章金鑰。

## 第一版的實際範圍

Dashboard → 進階設定 → 程式更新：

1. 使用者按「檢查新版」；預設不在背景檢查或下載。
2. 取得公開 `update-manifest.json`，用包內固定 Ed25519 公鑰驗證簽章。
3. 按「下載並驗證」：固定官方 GitHub repo／tag／asset，串流下載，檢查大小及 SHA-256。
4. 解壓前拒絕 traversal、Windows 裝置名稱、大小寫重複、symlink 與容量超限；解壓後再執行既有完整 manifest 校驗。
5. 新包位於目前 Data 根的 `updates/prepared-*/app`；原程式和 Data 不被修改。
6. 頁面提供 PowerShell 升級命令。**由使用者執行**；不自動要求 UAC、不自動停止 Host。

下載期間頁面每兩秒讀取一次後端進度：分卷下載顯示已收位元組／總大小、百分比與卷數；分卷合併校驗、完整 ZIP 雜湊、解壓各顯示自己的百分比。最終程式檔案清單驗證會顯示目前階段，因為該驗證器沒有可安全回報的逐檔進度。百分比只根據實際下載／處理的位元組計算；完成後才顯示「更新包已驗證」與安裝命令。整段準備流程不會停止監控。

更新 HTTP 失敗只保存例外類型，不輸出網址回應中的秘密。路由沿用登入與 CSRF。沒有 GitHub Token 放在包內。

**未實作：** 無人值守自動切換、安裝失敗的自動版本／資料庫回退、差分更新、私人 repo 下載授權。這不是「全自動線上更新」。未發布新版時檢查會明確失敗，不偽裝沒有更新。

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
