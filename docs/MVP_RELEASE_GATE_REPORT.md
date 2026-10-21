# MVP Release Gate：24H 基礎監控收尾

> **2026-10-17 文件路由**：此頁為歷史MVP報告，不作當前版本／部署／產品完整度判定。最新接線矩陣與正式1.0門檻見 [PRODUCTIZATION_SPEC.md](PRODUCTIZATION_SPEC.md)，執行順序見 [CURRENT_GOALS.md](CURRENT_GOALS.md)。沒有在本次文件整理重新執行歷史驗收。

**報告日期：** 2026-08-01
**結論：** `NOT_READY_FOR_PUBLIC_RELEASE`
**判定原則：** 沒有實機或長時間證據的項目保留 `not_checked`，不以 README、Mock 或短測推論成已完成。

> 2026-09-21 更新：此報告下方多數為原始 MVP gate 快照，不代表今日完整狀態。
> 已建立含內附 Python／FFmpeg／Node／模型的可攜包，封裝內 CPU 真實圖片推論與
> Dashboard localhost 啟動通過；排程 XML 已修正並由 Windows COM 在記憶體內解析，
> 但尚未註冊或通過重開機、SYSTEM 憑證、MX250、真實 Telegram 及 12–24 小時驗收。
> 最新狀態以 [PROJECT_PROGRESS_REPORT.md](PROJECT_PROGRESS_REPORT.md) 為準。

> 2026-09-23 更新：本報告新增第 8 節作為當前驗收狀態；第 2–6 節保留原始
> 2026-08-01 稽核快照，與第 8 節不一致時以第 8 節為準。

## 1. 本次變更

- `AGENTS.md` 維持精簡，加入開發環境不得偷偷修改 Python／驅動／CUDA、發布包與終端使用者邊界、Runtime Priority、SQLite 路由。
- SQLite 新增 schema version（目前 V2）、可重跑欄位 migration、WAL、busy timeout 與 bounded locked/busy retry。
- `FfmpegFrameSource` 新增有限指數退避、FFmpeg 子程序重啟、結束碼與遮罩後 stderr 摘要。
- 新增 `core/health` 的磁碟空間檢查、明確事件目錄保留與 RotatingFileHandler 工具。
- 新增 `tools/build_portable.py`：只驗證建置者提供的完整 Python runtime、必要套件、內含 FFmpeg 與模型；不自動安裝任何元件。
- 新增 `tools/create_task_scheduler_xml.py`：只產生 XML，不自行註冊或修改 Windows 工作排程器。
- 更新 README、PRD、架構、安全、相容性、測試、路線圖與 V0.2B 文件，將身分／行為明確標成 `experimental`。

## 2. MVP 功能稽核

| MVP 項目 | 狀態 | 程式碼／測試證據 | 真實性界線 |
|---|---|---|---|
| 單一影音來源 | `implemented`（單元）／`needs_real_data` | `src/meowkeeper/core/media/sources.py` 的單一 `FfmpegFrameSource`；`tests/test_media_pipeline.py` | RTSP 與 YouTube 實機尚未本次執行 |
| YOLO11n cat-only | `implemented` | `frame_analysis.py` 固定 COCO class 15；`tests/test_real_yolo.py` 真實模型找貓與負面影像 | 家庭攝影機誤報率仍需驗收 |
| 壓縮影音環形快取 | `interface_only` | `CompressedRingBuffer` 與 `tests/test_v02b_priority.py` | 尚未接入 FFmpeg 封包／事件 MP4 主迴圈 |
| 可配置事件前／後錄 | `interface_only` | `MediaBufferSettings` 驗證與設定契約 | 尚無可播放 ClipAssembler |
| Telegram 圖片通知 | `implemented`（明確啟用） | `AnalysisPipeline`、`V02AnalysisPipeline`、Telegram 單元測試 | 本次 Token 未啟用，未做實機傳送 |
| Telegram MP4 事件通知 | `not_implemented` | 尚無 MP4 send／片段組裝主流程 | 不以圖片通知代替 |
| 冷卻去重 | `implemented` | `CooldownGate` 與 pipeline 測試 | 只抑制通知，不刪事件 |
| 自動重連 | `implemented`（Mock）／`needs_real_data` | `BackoffPolicy`、來源恢復測試 | 網路中斷／RTSP 實機未測 |
| FFmpeg 異常重啟 | `implemented`（Mock）／`needs_real_data` | `FfmpegFrameSource` watchdog、失敗程序測試 | 尚未人工終止真實 FFmpeg |
| SQLite 事件保存 | `implemented` | `SQLiteStore`、identity correction 測試；schema V2/WAL 測試 | 私人資料庫不提交 Git |
| Windows 開機啟動方案 | `implemented_not_registered` | `create_task_scheduler_xml.py`、`Install-MeowKeeperPlus.ps1`、`Uninstall-MeowKeeperPlus.ps1` | manifest 驗證、App／Data 分離、安裝／升級／解除安裝與 COM XML 解析已驗證；尚未實際註冊或重開機驗收 |
| CPU fallback | `implemented` | `frame_analysis.py`／BoT-SORT device guard；實際 YOLO CPU probe | MX250 性能仍需實機 |
| 24H 健康狀態與繁中日誌 | `interface_only` | scheduler metrics、繁中警告、`core/health` 工具 | 尚無 24H service health dashboard／壓測資料 |

## 3. 穩定性稽核

| 項目 | 本次結果 |
|---|---|
| RTSP／YouTube exponential backoff | 有界實作與單元測試；實際來源 `not_checked` |
| FFmpeg process watchdog | 有界重啟與錯誤摘要實作；真實人工終止 `not_checked` |
| latest-frame-wins | `LatestFrameQueue` 契約與測試通過；尚未由完整服務接線 |
| AI 佇列最大長度 | `GPUInferenceScheduler`、V0.2B 測試通過 |
| Telegram 非同步傳送 | V0.2 `NotificationDispatcher` 與非阻塞測試通過；V0.1 CLI 仍是同步圖片流程 |
| SQLite WAL／busy timeout／schema version | 已實作且重開資料庫測試通過：journal=`wal`、busy timeout=5000 ms、schema=2 |
| SQLite bounded retry | locked/busy 有界重試已實作；未做多程序壓力實機測試 |
| 日誌輪替 | `core/health.configure_rotating_logging` 工具與契約；主服務接線 `not_checked` |
| 影片／日誌保留期限 | `cleanup_retained_files` 工具測試通過；正式事件保存接線 `not_checked` |
| 最低剩餘磁碟空間 | `inspect_disk_space` 工具測試通過；低空間主流程停錄策略 `not_checked` |
| 記憶體／程序釋放 | FFmpeg close、scheduler／dispatcher close 有單元涵蓋；24H leak rate `not_checked` |
| 正常關閉／崩潰恢復 | CLI 正常 close 有路徑；Windows 服務崩潰自復原 `not_checked` |

## 4. 執行證據

### 自動驗證

```text
python -m unittest discover -s tests -v
Ran 74 tests ... OK

python -m ruff check src tools tests
All checks passed!

python -m compileall -q src tools tests
通過（無輸出）
```

### 真實模型／影像／影片

- `tests/test_real_yolo.py` 載入專案 `models/yolo11n.pt`，真實含貓影像找到 COCO cat class 15；`bus.jpg` 為非貓負面測試且不產生貓咪事件。
- `tools/gpu_probe.py --run-model-tests --output reports\mvp_gate_gpu.json`：YOLO 實際後端 `CPU`，單張 cat latency 約 **2225 ms**；DINOv2 實際後端 `CPU`，約 **502 ms**。
- 開發機探針：Windows 10 build 19045、Python 3.10.11、RAM 31.87 GiB、RTX 2060 6144 MiB；PyTorch `2.13.0+cpu`，`torch.cuda.is_available() = false`。ONNX Runtime 雖列出 CUDA provider，不能因此宣稱本次 `.pt` YOLO 使用 CUDA。
- 本地 `reports\cat_test.mp4` 端到端乾跑 3 frames：YOLO CPU、產生 1 個貓事件、無 Telegram Chat ID，因此外部傳送數為 0。
- V0.2 本地乾跑 5 frames：影音來源仍持續，2 次 YOLO、3 張普通 AI 影格由負載控制略過、4 次行為 observation、SQLite `schema_version=2`／`journal_mode=wal`；這不是長時間證明。

### 安全／缺資料行為

- `run_phase0.py --profile mx250_minimum --output reports\mvp_gate_phase0.json` 在未提供 RTSP、Token 時回報 `not_checked`，不猜測路徑、不傳送訊息。
- Telegram 關閉測試與缺少 Token 測試通過；本次實機 Telegram 成功／失敗均為 **0／0（未啟用）**。
- `test_ffmpeg_network_source_restarts_with_exponential_backoff` 以失敗程序驗證重啟 1 次與退避；真實 FFmpeg 人工終止恢復為 `not_checked`。
- `tools/build_portable.py --dry-run` 的契約測試確認必須明確提供 Python runtime；工具不下載、不執行 pip、不修改驅動／CUDA。當前 repository 沒有 `runtime/python`，因此正式包建置為 `not_checked`。

## 5. 24H 指標

| 指標 | 結果 |
|---|---|
| 實際推論後端 | **CPU**（PyTorch CPU build）；非 CUDA 證據 |
| 平均 CPU／記憶體使用量 | `not_checked`；目前只取得探針瞬時 RAM，未執行資源採樣器 |
| GPU 是否真正使用 | **否，本次 YOLO／DINO 實測 CPU**；RTX 2060 不代表 MX250 |
| FFmpeg 重啟次數 | 真實 24H：`not_checked`；Mock watchdog：1 次 |
| Telegram 成功／失敗次數 | 0／0（本次關閉 Telegram） |
| 丟棄 AI 影格數 | 本地 V0.2 5-frame dry-run：3；壓縮影音快取丟棄：無可用實機數據 |
| 錄影完整性 | `not_checked`；目前只有壓縮片段／最新影格契約測試，無事件 MP4 驗收 |
| SQLite 大小 | `reports\mvp_gate.sqlite3` 約 73,728 bytes（短測，不是成長率） |
| 磁碟成長速度 | `not_checked`；探針當時剩餘約 397 GiB |
| 2 小時自動壓力測試 | `not_checked` |
| 12～24 小時 MX250 實機驗收 | `not_checked` |

## 6. Gate 判定與下一步

目前可進入的是「開發／內部整合測試」，不可進入 12～24 小時實機驗收或對外發布。進入
下一個 gate 前仍需：

1. 在目標 MX250／8 GiB（建議 16 GiB）機器，以使用者提供且已確認的 RTSP 554 路徑做至少 2 小時中斷／恢復測試。
2. 將壓縮片段接入真正的事件 ClipAssembler，驗證前／後錄、關鍵幀、MP4 播放與 Telegram 大小限制。
3. 在全新 Windows 10／11 主機實際驗收 UAC 安裝、Task Scheduler 開機啟動、崩潰重啟、
   登入後 Tray 與可保留資料的解除安裝；腳本與 `-WhatIf` 已完成。
4. 接上日誌輪替、事件保留、最低磁碟策略與健康狀態採樣，保存 CPU／RAM／FFmpeg／Telegram 指標。
5. 以家庭攝影機實拍資料另外驗證身分／行為 experimental 結果；在此之前不得宣稱嗨啵、Saki 或任何行為已完成。

因此本次完成的是「穩定性基礎與文件真實性收尾」，不是公開發布核准。

## 7. 2026-09-23 安裝閘門補充

- `passed`：Windows 目前使用者安裝、安裝後 manifest、Dashboard health、Host／Tray 單一實例、
  embedded runtime 路徑、App／Data 分離與登入後 Startup。
- `not_checked`：UAC 系統級 ProgramData 安裝、SYSTEM 開機觸發、重開機後無登入監控；UAC 兩次
  由使用者取消，沒有建立系統排程或 ProgramData 安裝。
- 2026-09-23 再次以 r2 執行系統安裝預演，manifest 與 Task XML 產生通過；正式 `RunAs` 回報
  `The operation was canceled by the user`。事後唯讀確認 `C:\ProgramData\MeowKeeperPlus` 不存在，
  Core／Tray SYSTEM 工作不存在，沒有發生系統級安裝；目前使用者版、獨立 Data 與 Startup 仍有效。
- `not_checked`：真實 Telegram、MX250 與 12–24 小時家庭環境，因此發布閘門仍為
  `MVP_CODE_COMPLETE`／`MVP_REAL_E2E_PENDING`。
- 後續唯讀複核：目前使用者 Dashboard setup=200、未登入保護 API=401、首次設定回歸 7/7 通過；兩個使用者提供的攝影機 LAN 位址 TCP/554 均不可達。此項不是 MIoT 雲端串流測試，不替代 `.55` 舊記錄的目前版重驗。
- 安裝版 YouTube 公開直播 bounded smoke：8 張真實影格 YOLO CPU run 正常結束；30 張 live run 產生 3 個 FFprobe 可讀 H.264 640×360 ring 段，SQLite 無事件、Telegram 關閉。這只是短時來源／快取驗證，不替代直播重連、貓事件或 Telegram 外送。

## 8. 2026-09-23 當前 MVP 與本地事件影片驗收

### 目前功能狀態

| MVP 項目 | 當前狀態 | 本機證據 | 尚未驗證 |
|---|---|---|---|
| 單一來源接收與共享影格 | `implemented` | YouTube 公開測試頁及本地影片取得影格；單一 `FfmpegMediaIngest` | 家庭 RTSP／YouTube 的持續重連與長時間運行 |
| YOLO11n cat-only | `implemented` | 真實模型本地影像與影片找到貓；非貓 `bus.jpg` 負面測試 | 家庭畫面準確率及 MX250 效能 |
| 壓縮環形快取及輪替 | `implemented` | `DiskSegmentRingBuffer`、引用保護與實際 FFmpeg MPEG-TS | 真實家庭來源的長時間容量曲線 |
| 可配置事件前／後錄與 MP4 | `implemented` | `EventClipService`／`ClipAssembler`；本節安裝包 E2E | RTSP／YouTube 來源的音畫同步與長時間關鍵幀行為 |
| Telegram 圖片／MP4 | `implemented`（需明確啟用） | 事件與通知佇列、停用狀態及 fake notifier 測試 | 真實 Bot／Chat ID 的 `ok=true`、`message_id` |
| SQLite 事件保存 | `implemented` | schema version 3、WAL、媒體事件完成狀態 | 多程序長時間忙碌重試與正式資料保留曲線 |
| 冷卻去重 | `implemented` | 本地 E2E 有 48 筆事件合併，避免重複組片 | 家庭實際事件頻率的門檻校正 |
| 自動重連／FFmpeg watchdog | `implemented`（自動化測試） | 有界退避及 fake forced-exit 測試 | 真實網路拔除／FFmpeg 人工終止後恢復 |
| Windows 啟動 | `implemented`（目前使用者登入後） | Startup CMD、Host=1、Tray=1、embedded Python、Dashboard health 200 | SYSTEM 預登入啟動、重開機與 UAC 系統級安裝 |
| CPU fallback | `implemented` | 本輪安裝包 YOLO backend=`CPU` | MX250 CUDA／CPU 性能、溫度與長期記憶體 |
| 24H 無人值守 | `partial` | Host health 與本機安裝已驗證 | SYSTEM service、資源指標、12–24 小時與崩潰恢復 |

### 安裝包內真實本地影片 E2E

- 最終安裝包路徑：`%LOCALAPPDATA%\MeowKeeperPlus\App`；測試來源為 repository 的合法本地
  `reports/phase3a-smoke/cat30.mp4`，資料與輸出寫入使用者 Temp，不觸碰正式事件資料庫。
- 內附 Python／YOLO11n／FFmpeg 實際執行 50 個影格，backend=`CPU`，偵測到 2 隻貓，
  confidence=`0.9249`。
- SQLite 有 2 筆 `cat_detected`、`clip_status=completed`、`telegram_status=disabled`、
  `telegram_attempts=0`；另 48 筆在事件合併窗內標記 `merged_cooldown`。
- 兩個事件 MP4 均由內附 FFprobe 驗證可讀：H.264、640×480、14.2 秒、478,184 bytes。
- 測試找出並修正 stream-copy 長 GOP 最後分段索引時間窗問題；分段 index 以 ingest session
  時間涵蓋 writer 尚未關閉時的事件。輸出影片實際時長仍以 FFprobe 結果為準。
- 這是安裝包上的本地影片鏈路驗證；未測試家庭 RTSP、家庭 YouTube 長時間直播、真實
  Telegram、MX250、12–24 小時連續服務，也不構成公開發布核准。

### 當前完整程式驗證

```text
python -m unittest discover -s tests
Ran 198 tests ... OK

python -m ruff check src tests tools
All checks passed!

python -m compileall -q src tests tools
通過
```

目前仍維持 `MVP_CODE_COMPLETE`／`MVP_REAL_E2E_PENDING`。Dashboard 首次設定尚未完成；
未設定時所有 Dashboard 保護頁均導向 `/setup`，首次設定頁僅允許 localhost；
需由使用者建立至少 3 字元的管理者帳號及至少 12 字元密碼，再前往 `/login`。
無 Session 的受保護 API 仍回 401。管理者建立前無法進行已登入來源／Telegram 設定。
Telegram 真實測試時，Token 與 Chat ID 僅在 Dashboard 安全輸入，不應貼到 Codex 對話或日誌。

## 9. 2026-09-23 r4 安裝升級安全補充

- `-CurrentUser -Upgrade` 先驗證舊 App，並在任何 Startup／App 刪除前完成精確程序辨識、handle／程序資訊複核、停止等待及殘留重掃；錯誤均 fail-closed。PowerShell 5.1 parser 通過；隔離測試覆蓋錯誤命令列、缺少程序資訊、CIM 失敗與 handle 執行檔不符。沒有停止目前 Host／Tray，成功終止真實 Host 的測試為 `not_checked`。
- r4 可攜包 verifier 通過（18,657 個檔案；`private_data_present=false`）。包內 system-mode `-WhatIf` 因目前使用者 App／Startup 標記回報 `blocked`、`system_changed=false`；未執行 UAC、未註冊 SYSTEM 工作。
- 本次最新實機狀態：仍是 r2 目前使用者版，Host PID 20216、Tray PID 20600；Dashboard `/healthz=200`、`/setup=200`、認證狀態 `configured=false`。r4 尚未安裝。此項只驗證目前使用者服務存活與新包完整性，不代表首次設定、真實 Telegram、家庭攝影機、SYSTEM 開機或 24H gate 通過。
- 發布閘門保持 `MVP_CODE_COMPLETE`／`MVP_REAL_E2E_PENDING`；首次管理員設定、Telegram 真實 message_id、家庭 RTSP／YouTube 含貓通知、MX250 及 12–24 小時驗收仍未完成。
