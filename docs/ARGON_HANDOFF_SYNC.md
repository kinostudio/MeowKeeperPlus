# Argon 工作區同步

每次交接前，先完成本次檢查與交接文件，再以既有授權的 Aaron Windows 身分執行：

```powershell
Set-Location -LiteralPath 'C:\Users\Aaron\Documents\Codex\MeowKeeperPlus'
& '.\.venv\Scripts\python.exe' -X utf8 tools\sync_to_argon.py --dry-run
& '.\.venv\Scripts\python.exe' -X utf8 tools\sync_to_argon.py
```

需要核對已安裝發布基準時，兩個命令皆加上 `--baseline-package 'dist\release-r111\MeowKeeperPlus-r111-windows-x64.zip'`（依實際基準包調整）。ZIP 只在本機讀取 manifest 與必要第一方檔案，不傳送發布包。若遠端正式包 manifest 與本機基準不相同，或工作區缺少基準檔案，會停止。

SSH 固定連線至 `user@100.101.105.43`；傳送內容僅寫入 `C:\Users\user\Desktop\MeowKeeperPlus-Workspace`。`HANDOFF_ARGON_*.md` 另複製到工作區根目錄與該帳戶桌面。SSH 使用既有私鑰檔案路徑，Python 腳本不開啟或複製私鑰；預設路徑是目前執行帳戶的 `%LOCALAPPDATA%\Codex\MeowKeeperPlus\ssh\shrimp-user-ed25519`，也可傳 `--identity` 指定既有授權路徑。既有主機指紋必須匹配，不自動接受新指紋。

## 差量及 Git 快照

- 候選清單來自 `git ls-files --cached --others --exclude-standard`，包含未提交與未追蹤的第一方程式、測試及文件。
- 排除 `.git`、runtime、vendor、models、data、dist、build、venv、快取、日誌、秘密與大型二進位；單一來源檔案上限2 MiB。`.env*`、私鑰／DPAPI、資料庫、ZIP、模型檔不傳；只豁免公開驗證公鑰 `configs/update-public.pem`。
- 讀取遠端 `.argon-sync-manifest.json` 並重新核對已管理檔案的實際 SHA-256，只上傳新增或不同內容。傳輸資料經 gzip 壓縮，分批傳送並顯示進度；小型請求使用 SSH stdin，超過8 KiB 則使用 SSH／SFTP 傳到工作區內的隨機暫存檔，處理後刪除，避免 Windows OpenSSH 長行輸入阻塞。
- 刪除僅針對上一份同步 manifest 管理過、目前已不存在的允許路徑。首次同步無 manifest 時，不刪遠端不明來源檔案；也不刪桌面上的其他資料。空目錄可保留。
- 更新 `.argon-git-meta.json`：分支、HEAD、UTC同步時間、完整 `git status --short` 及可選的基準核對摘要。
- `ARGON_UNCOMMITTED.diff` 包含允許範圍內相對 HEAD 的已暫存與未暫存差異，另附未追蹤文字檔新增內容；大型／秘密排除規則同樣套用，二進位新增只標示名稱。不能為了「完整 diff」而附帶已排除的秘密或模型。

遠端寫入前檢查範圍、路徑穿越、Windows reparse point、來源是否在盤點期間改變，以及遠端檔案是否在盤點後被其他人修改。每檔原子替換，完成後重新核對整份目標 SHA-256，再更新同步 manifest。若中途失敗，舊同步 manifest 仍保留，可修正原因後重跑；不宣稱整批檔案具有交易回滾。

## 正式 Host 邊界與版本依據

同步 helper 僅為基準核對唯讀取得正式 `App\build_manifest.json` 的 SHA-256，不修改 `C:\ProgramData\MeowKeeperPlus\App`，不安裝、不重啟、不改監控或通知。Argon 工作區不含 Python runtime、模型或攝影機資料，不能直接當作可攜發布包啟動。

分支名稱不等同已發布版本。若發布包沒有 Git commit／tag 證據，應如實記錄為「發布包第一方檔案基準比對」，不可聲稱完成 Git 祖先驗證；未提交修改由同步快照與 diff 提供給 Argon。

驗證：`python -m unittest tests.test_argon_sync tests.test_vision_settings -v`；另以實際 SSH dry-run／同步結果確認傳輸與遠端雜湊。
