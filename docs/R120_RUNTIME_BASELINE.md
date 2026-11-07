# r120：已使用安裝版的差量基準

r119 的乾淨套件重建驗收未涵蓋已啟動安裝版的執行期產物，可能在下載 ZIP 前被「基準外檔案」保護阻擋。r120 保留 r119 的 Telegram 接收器恢復及前版累積修正，精準處理這項交付缺口。

## 可辨識的產物

- `data/matplotlib/fontlist-v<數字>.json`：最多 4 MB，版本須符合檔名，且字型清單欄位型別正確。
- `data/ultralytics/settings.json` 或 `data/ultralytics/Ultralytics/settings.json`：最多 64 KB，版本與目錄欄位型別正確。
- `logs/go2rtc.log`、`logs/go2rtc.log.1`：只接受一般檔案，不讀取日誌內容。
- 原有精確鎖檔規則保持不變。

這是安裝基準的分類，不是對整個 Data／logs 放行。所有路徑仍先拒絕重新解析點；未知檔案、損壞／超限 JSON 或任一程式雜湊不符，仍在 ZIP 下載前停止，沒有完整包回退。新 App 只重建簽章清單中的檔案，執行期產物不被複用、打包或上傳；原安裝不因準備流程被刪檔或更動。

一次性過渡工具及受保護 worker 共用相同規則。`DELTA-INSTALLED-RUNTIME-OUTPUTS` 覆蓋準備、實際重建與 worker staging、未知檔案下載前拒絕、大小／格式／重新解析點及程式異動拒絕。候選驗收另在 r116 隔離基準加入合成的已使用痕跡，不使用私人設定或日誌。

## 驗收界線

正式安裝仍由使用者執行；不自動安裝、不清除現有快取、不覆寫 r119 發布資產。完整 1,089 項／回歸 188 項零略過、候選 21,358 檔完整性、帶合成執行期產物的簽章重建、原 r116 runtime 準備、包內 Python 4 項新增邊界測試及 HTTP／公開下載簽章驗證均通過。ZIP 1,375,468 bytes、31 檔、無大型依賴；[r120 公開候選](https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r120)與[更新命令](DELTA_HOTFIX.md#r120-更新命令)。正式 SYSTEM 更新與正式資料回滾仍待實機驗收。
