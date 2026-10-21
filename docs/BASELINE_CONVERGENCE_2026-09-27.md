# 2026-09-27 版本收斂與真實通知驗收

狀態：進行中；完整產品目標尚未完成。

## 已驗證

- 初始 Git 基線：`6d68102`，278 個原始碼、測試、文件、素材及供應鏈 manifest 檔案。
- 未提交私人 runtime 資料、憑證、報告、下載模型或執行檔。273 個文字候選檔案的 Token／私鑰／Xiaomi credential 格式掃描為 0 命中；此掃描不是完整安全稽核。
- 全套 unittest：230 項通過；`ruff check src tools tests`、`compileall src tools tests` 通過。
- 目前使用者 Credential Manager 可讀到已保存的 Telegram 設定，勾選通知對象 1 個。
- 真實 Telegram 文字測試：`ok=true`，`message_id=14`。不保存 Chat ID、別名、Token 或完整回應。

## 執行環境發現

- Dashboard PID 10004 使用 repository `.venv`，啟動參數含 `--dev --analyzer noop`，不是 YOLO 監控驗收啟動。
- 該 Host 資料位於工作區 `work/live-host`；管理員檔案沿用 ProgramData Data。升級時不可把這些不同路徑混成同一份設定。
- localhost 8000 的 health 正常，但 core_status 為 stopped。
- go2rtc PID 24092 持有 localhost 1984/8554；程序完整路徑在本輪可用權限下未取得，不可據 PID 名称直接終止。
- ProgramData 安裝內容與 repository 的六個抽查關鍵檔案不一致。

## 待完成

1. r16 建置與 manifest 驗證，對照 Git 基線。
2. 同版 Host 啟動並驗證 YOLO，而非 noop；保留既有設定及攝影機 Session。
3. 真實小米找貓事件 → SQLite → JPEG／MP4 → Telegram message_id。
4. 真實兩小時／中斷恢復／重開機，再進入 12–24 小時。
5. PTZ 實機控制、V0.2 主管線與統計前端，之後聲音與隱私自動化。

文字測試成功不能替代照片、影片或貓咪事件端到端驗收。

## r16 執行與媒體通知進展

- r16 可攜包建置完成：18,669 files；內附 verifier `passed`、`private_data_present=false`，模型 SHA-256 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`。
- 抽查全部 188 個 src/tools/scripts 包檔案：除安裝 PS1 建置時預期的編碼轉換外，來源 hash 一致。ProgramData 舊安裝尚未覆寫。
- 精確停止已識別的 noop Host PID 10004 及其 FFmpeg，改以 r16 embedded pythonw 啟動 PID 17884，analyzer=yolo、既有已勾選 Telegram 啟用。沿用相同資料與管理員路徑，未停止 go2rtc。
- 新 Host 持續更新真實小米 JPEG，子程序 FFmpeg PID 31344。`healthz.core_status` 回傳 stopped 的原因是 façade health 只讀其自身狀態，沒有查 controller callback；不能以此斷言影音停止，需修正。
- 畫面為真實住家外景，當時未見貓，SQLite 尚無 cat event。不得把以下傳送測試算作找貓事件閉環。
- 使用既有 JPEG／已完成的 ring segment，沒有第二條攝影機連線：JPEG `ok=true, message_id=15`；MP4 `ok=true, message_id=16`。
- MP4 FFprobe：H.264、640×360、3.0 秒、238,706 bytes。傳送完成後私人測試影片的臨時目錄已清除。
- 兩小時牆鐘唯讀 observer PID 23856 已啟動，監測 Host PID 17884；證據在工作區 `work/r16-soak-samples.jsonl`。啟動不等於通過，仍待完整 7,200 秒結果；故障注入及重開機未執行。
- 另發現 EventClipService 未保存通知 API 回傳的 message_id；正式事件收件證據仍需補上。

## repository 後續修正（尚未部署至觀測中的 r16）

- health 改為讀取 controller status callback，避免 Host 直接啟動監控後 façade 留在 stopped；callback 失敗時回 unknown，不回傳錯誤秘密。
- SQLite schema 4 新增可重跑的 telegram_receipts 表，保存 event_id、SHA-256 收件人參照、媒體種類、message_id 及收件時間；不保存 Token 或 Chat ID 原值。
- 事件影片／截圖收件結果接入此表；多收件人重試跳過已成功送達者。DB 保存失敗不觸發已成功通知的網路重送，並保留警告。
- 42 項相關測試通過，包括 v3 遷移保留事件、重跑 migration、真實控制器 health 同步與多收件人重試去重。全範圍 Ruff 通過。
- 觀測中的 r16 不受 repository 修正影響；完成這輪後仍需封裝、切換並驗證新版本，不能將新程式碼視為已部署。

## 通知選擇與 PTZ 建置補充

- 修正已保存收件人清單全部停用、空清單或無法解析時回退到舊 Chat ID 的問題。沒有選取對象時停用外送，不阻擋媒體啟動；未建立新版清單的舊設定仍可使用。9 項相關測試及變更範圍 Ruff 通過，未對外傳送測試訊息。
- 固定 PTZ fork commit `e746789ecbcfe663d6e22a69eac586813b4cb575` 已以本機 Go 1.25.6、離線 module cache 編譯；`internal/xiaomi`、`pkg/xiaomi/miss` Go 測試通過。
- 原始碼 ZIP SHA-256：`A3EFB712CD0678EDAED380CC5760D1B8C8CB9CFDAA7FC557276F8C8FD40829F3`。
- 實驗檔案：`reports/ptz-fixed-source/go2rtc-ptz-e746789.exe`；SHA-256：`3AFA1B357D80E49A8149C34FB663BA2E0803709172D6B98AA0A25F09FB8F33CF`。
- 實驗版尚未啟動，沒有替換正式 vendor binary；編譯成功不代表實際方向控制成功。長時間觀測中的既有 Host／FFmpeg 保持運行。

## V0.2 正式 controller 本地影片與通知閉環

- 在獨立 Temp 資料目錄使用真實 `cat30.mp4`、正式 RepositoryCoreController、`analyzer_mode=v02`、內附 FFmpeg。
- Telegram disabled：CPU、無 V0.2 fallback、兩筆已結束行為候選、一筆 completed 媒體事件，傳送嘗試 0。
- 再以已勾選收件人明確啟用一次真實傳送，caption 標示本地測試影片而非即時攝影機警報：同一事件工作流產生 MP4，telegram_status=sent、telegram_attempts=1，SQLite telegram_receipts 保存 video message_id=17。
- FFprobe 驗證 H.264、640×480、30.0 秒。前 5／後 10 秒設定不等於精確片長；此素材 stream-copy 長 GOP 產生完整 30 秒，沒有虛報 15 秒。
- 測試結束後 Temp DB／影片清理；遮罩收件及媒體證據保存在工作區 `work/v02_controller_telegram_e2e.json`。沒有在報告保存 Token、Chat ID 或私人監控影片。
- 此項證明真實本地影片經 Host 到 Telegram 的自動工作流，不代替小米真實貓事件、家庭行為準確率或 24H 驗收。
