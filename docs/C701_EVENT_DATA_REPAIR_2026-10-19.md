# C701 安裝版事件影片與資料根目錄實機修復

## 已確認的現況

- 遠端 SHRIMP-ASUS 正式 r107 `/api/v1/status.metrics.disk.path` 回報 `C:\ProgramData\MeowKeeperPlus\App\data\events`，不是外層 `Data\events`。
- 事件 API 的最新三筆 `clip_path` 仍指向桌面 r98 的 `data\events`，`clip_exists=false`，但檔案在舊目錄及新目錄皆存在。原因是正式版只允許目前事件根目錄內的影片路徑；複製資料庫不會改寫其中的絕對路徑。並非已證實的轉碼或權限故障。
- `App\data`、外層 `Data`、r98 `data` 均有 840 筆事件、748 支 MP4、2 份寵物 profile。數量一致不等於所有內容一致，不可因此刪除其中任何一份。

## 本輪修復

使用 `tools/repair_event_media_paths.py`，只處理正在使用的 `App\data\meowkeeper.db`：

1. 以安全事件 ID 對應目前 `events\event_<id>.mp4`。
2. 驗證檔案大小與資料庫原有 SHA-256；缺雜湊或不相符一律略過。
3. SQLite backup API 備份完整資料庫（含已提交 WAL 狀態）。
4. 交易內條件更新 `clip_path`；不改通知、人工回報、AI 結果或影片本體。

實際結果：745 筆驗證、745 筆更新、0 筆雜湊不符、95 筆沒有對應 MP4。
備份：`C:\ProgramData\MeowKeeperPlus\App\data\repair-backups\event-paths-20261018T212945Z-45a848f7.db`。

95 筆未有對應影片的紀錄：93 筆 `completed`、1 筆 `failed`、1 筆 `failed_with_snapshot`。沒有把缺片宣稱修好；93 筆已完成紀錄仍須由保留的其他備份追查／還原，不能由資料庫重建影片。

## 實機與隔離驗收分開

- 正式事件頁重新載入後，已出現「播放事件影片」。
- 最新事件人工回報視窗 `readyState=4`，640×360，相容播放影片約 18 秒；按「播放選取片段」後 `paused=false`，後續時間到尾端且 `ended=true`，無媒體錯誤。此為一支真實歷史事件驗收，不代表逐支播放全部 745 支。
- 人工回報顯示現有兩份寵物選項。
- 本機 19 項路徑、遷移、影片與修復測試通過；新工具與測試 Ruff 通過。
- 未安裝、未覆寫正式 Host 程式、未重啟監控；正式版防止下次搬移失效的事件 ID 回退仍需由使用者安裝後續新版。

## 哪一個 data 正確

- **目前這台正在運行的 Host**：`App\data`；寵物是 `App\data\pets`。依現況先保留，不能搬走。
- **正式安裝版長期設計**：`C:\ProgramData\MeowKeeperPlus\Data`，與可替換的 `App` 分離。
- **可攜版**：可攜包內 `data`。

兩處 data 來自安裝器使用外層 Data、舊版相對路徑啟動仍用 App\data 的不一致，並非要求使用者維護兩套資料。後續統一需停止相關寫入、完整備份、核對衝突及修正啟動參數，再遷移；本輪沒有自動合併兩份可能分歧的資料庫。現有 repository 已有 `runtime_paths.py`、安裝器遷移工具及回歸門檻，原始碼通過不代表遠端已部署。
