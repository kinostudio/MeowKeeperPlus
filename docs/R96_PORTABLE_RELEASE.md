# r96 可攜入口與 Dashboard 修正

發布包：`dist/MeowKeeperPlus-portable-2026-10-13-r96`。不自動安裝或升級。

## 一鍵入口

`MeowKeeperPlus.cmd` 使用內附 Python 呼叫 `portable_start.py`，啟動或沿用 Tray，由 Tray 啟動或沿用核心，再開啟 Dashboard。通訊埠讀取設定，不依賴系統 Python、venv 或 pip。重複開啟沿用 Tray，並重新開啟 Dashboard。

核心單獨入口 `MeowKeeperPlusDashboard.cmd`、Tray 單獨入口 `MeowKeeperPlusTray.cmd` 保留。

## 驗證與限制

- 完整 unittest 584 項通過；另外新入口及核心綁定 4 項測試通過；Ruff、compileall 通過。
- 完整來源碼的 Telegram Token 格式掃描 0 命中；不能據此宣稱涵蓋所有秘密類型。
- 隔離 Windows 執行真正發布包 CMD 兩次，entry exit=passed，Tray 峰值 1、觀察 PID 22104；沒有啟動攝影機／Telegram、未修改正式服務。測試預先啟動隔離 Host，這證明核心沿用，不代替首次啟動核心的端到端證據。
- 最終發布包完整性校驗通過：20,579 檔、private_data_present=false，YOLO hash 不變；結果見 `reports/r96-verify.txt`。第一次校驗拒絕測試衍生的 xiaomi-regions／xiaomi-setup cache，已只從新包清理這兩個臨時狀態檔後重新通過，未修改 manifest 或正式資料。
- 收錄 Dashboard／照片管理／日誌／位置診斷修正，不代表 14 項功能均已實機驗收。
- C701 本地主機的 Telegram `/pos1` 仍未通過；Aaron 的定位試驗不得當成 C701 證據。未宣稱指定位置已修好。
- CPU Windows 感測器後端仍未接通，不以 GPU 溫度冒充 CPU 溫度。

## C701 可攜版更新注意

可攜版不需要 SYSTEM 安裝。完整關閉舊程式後，把舊包 `data` 複製到新包，保留舊包作回退，再執行新包 `MeowKeeperPlus.cmd`。不要同時開啟新舊包。

小米 session／位置可能位於目前 Windows 帳號的 AppData，不能把它當成 package/data；同一帳號通常沿用，但跨電腦需重新確認。換可攜資料夾會改變 go2rtc 執行檔路徑，既有防火牆規則可能不再匹配；用包內防火牆診斷確認，修復仍需使用者授權。
