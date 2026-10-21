# MVP Phase 2：事件影片閉環驗收報告

**執行日期：** 2026-08-02
**結論：** `INTERNAL_INTEGRATION_ONLY`，尚未通過家庭環境或公開發布 gate。

> 2026-09-23 補充：stream-copy 遇到長 GOP 時，唯一可用的 TS 可能持續處於 writer
> 狀態。事件組片 worker 現在最多等待 60 秒讓片段完成；此等待不阻塞 ingest、AI 或
> Telegram dispatcher，逾時仍安全降級為事件截圖並記錄 `last_error`。

本報告只涵蓋壓縮環形影音、事件 MP4、`/live`、SQLite 媒體狀態與 Telegram 影片佇列。
身分辨識、行為分析、ONVIF/PTZ、GUI、新模型均不在本階段範圍。

## 1. 本階段新增與修改

### 新增

- `src/meowkeeper/core/media/segment_index.py`：分段 metadata、時間查詢與引用計數。
- `src/meowkeeper/core/media/ring_buffer.py`：磁碟 MPEG-TS 環形快取、時間／容量輪替、低磁碟警告。
- `src/meowkeeper/core/media/segment_recorder.py`：FFmpeg segment muxer 與有限 watchdog；RTSP 沒有
  本地 ingest 時拒絕第二條攝影機連線。
- `src/meowkeeper/core/media/ingest.py`：單一 FFmpeg 輸入同時輸出 JPEG AI 影格及壓縮影音分段。
- `src/meowkeeper/core/media/media_validation.py`：FFprobe 影像／音訊／時長驗證。
- `src/meowkeeper/core/media/clip_assembler.py`：concat + stream copy，失敗才轉碼，並計算 SHA-256。
- `src/meowkeeper/core/media/event_clip.py`：事件佇列、前後錄、`get_recent_clip()`、Telegram 背景佇列。
- `tests/test_phase2_event_video.py`：12 個分段、MP4、音訊、watchdog、Telegram 與故障隔離測試。
- `docs/MVP_PHASE2_MEDIA_PLAN.md`：媒體資料流與單一來源邊界。

### 修改

- `src/meowkeeper/storage/database.py`：schema version 3、新增 `media_events` 與組片／Telegram 欄位，
  保留 WAL、busy timeout、有限重試及跨背景工作者安全鎖。
- `src/meowkeeper/core/notifications/telegram.py`：新增 `send_video` multipart 介面。
- `src/meowkeeper/core/media/__init__.py`、`domain/settings.py`、`config.example.json`：公開媒體契約。
- `tools/run_monitor.py`：加入明確 `--event-video` 模式；預設行為不變，只有明確 `--send-telegram` 才外傳。
- `docs/ARCHITECTURE.md`、`docs/PRD.md`、`docs/TESTING.md`：由 interface-only 更新為本階段可驗證實作與限制。

## 2. 媒體架構與實際設定

```text
單一來源 → 一個 FfmpegMediaIngest
          ├─ 640 級、最高 5 FPS JPEG pipe → YOLO／事件觸發
          └─ H.264/H.265（可選 AAC）MPEG-TS 1 秒分段 → DiskSegmentRingBuffer
```

預設設定：

| 設定 | 值 |
|---|---:|
| 分段容器 | MPEG-TS |
| 分段長度 | 1 秒 |
| 環形保留 | 60 秒（可 10～120 秒） |
| 快取容量上限 | 512 MB |
| 一般事件 | 前 5 秒＋後 10 秒 |
| `/live` | 前 5 秒＋後 0 秒 |
| stream copy | 優先 |
| 長時間錄影格式 | 壓縮分段，不保存 BGR／JPEG 影格 |

事件分段在組片期間增加引用計數，輪替器不會提前刪除；組片完成或失敗後釋放引用。
關鍵幀造成的短片可能比請求時間略長，不為毫秒級精確度預設全段重編碼。

## 3. 事件與 Telegram 閉環

`EventClipRequest` 保存 `event_id`、觸發時間、事件類型、來源、track／pet（未知時為
`unknown`）、信心值及前後錄設定。流程是：建立 SQLite metadata → 鎖定分段 → 等待後錄 →
stream copy 組 MP4 → FFprobe 驗證 → 更新 `media_events` → Telegram 有界佇列。

`ClipAssembler` 的 stream copy 失敗才進入低負載 H.264/AAC 轉碼；Telegram 檔案超過 50 MiB
時保留原始影片，另產生暫時壓縮版，仍超限則改送事件截圖（若有）。任何 Telegram 失敗
都不刪除本地事件影片。

Telegram 送出前的相容性規則固定為：H.264 + `yuv420p`、有效寬高／時長且 MP4 `moov`
位於 `mdat` 前時直接上傳；HEVC/H.265、非 `yuv420p`、索引不合格或超過大小上限時，
 才產生 H.264/AAC、`yuv420p`、`+faststart` 的暫時副本。此副本傳送完即清理，原始
 事件影片不被覆寫。

`get_recent_clip(pre_seconds, post_seconds=0)` 是可由 CLI／測試呼叫的 `/live` 服務介面；
`record_from_now(seconds)` 為另一個從現在開始錄影的語意，不與 `/live` 混用。

## 4. Audit 結果

| 項目 | 狀態 | 證據／界線 |
|---|---|---|
| 單一 FFmpeg ingest 分流 | `implemented`（本地整合） | `FfmpegMediaIngest`；`run_monitor --event-video`；RTSP 路徑仍需實機 |
| 壓縮環形快取與輪替 | `implemented`（自動測試） | `DiskSegmentRingBuffer`、時間／容量／引用測試 |
| 事件 MP4 stream copy | `implemented`（本地素材） | cat MPEG-TS 片段組成可讀 MP4 |
| copy→轉碼降級 | `implemented`（契約＋轉碼本地測試） | `ClipAssembler` fallback 與 `transcode_for_telegram` |
| 有音訊／無音訊 | `implemented`（合成合法素材） | FFprobe 驗證音訊存在；D-Link 音訊仍需實機 |
| Telegram `sendVideo` | `implemented`（disabled／fake queue） | multipart 與背景佇列測試；真實 Chat ID `not_checked` |
| SQLite 媒體事件狀態 | `implemented` | `media_events`、schema version 3、WAL／busy retry |
| FFmpeg watchdog | `implemented`（fake forced-exit） | `SegmentRecorder` 有界退避；真實人工終止 `not_checked` |
| Telegram／DB 不阻塞快取 | `implemented`（元件測試） | 分離 clip／Telegram 佇列；24H 仍需實機 |
| 行為／身分辨識 | `experimental`／本階段不改 | 不作本階段驗收依據 |

## 5. 執行證據

### 自動測試與靜態檢查

```text
python .venv\Scripts\python.exe -m unittest discover -s tests -v
Ran 86 tests ... OK

.venv\Scripts\ruff.exe check --no-cache src tests tools
All checks passed!

PYTHONPYCACHEPREFIX=<temporary> python -m compileall -q src tests tools
通過

config.example.json、models/model_manifest.json
JSON 解析通過；秘密掃描沒有命中提供的真實 PIN／Token。
```

秘密掃描僅命中 `.env.example`、文件及測試中的明確佔位／遮罩字串；沒有把使用者提供的
攝影機 PIN 寫入程式、報告或設定檔。

### 真實本地含貓與非貓素材

- `reports\cat_test.mp4` + YOLO11n + `--event-video --device cpu --max-frames 5`：
  實際推論後端 **CPU**，找到 2 隻貓，產生 1 個 `cat_detected` 事件；Telegram disabled，
  外部傳送 0 次；事件 MP4 約 117,537 bytes、FFprobe 可讀、實際約 1 秒。
- `tests\assets\bus.jpg` + YOLO11n + `--event-video --device cpu --max-frames 1`：
  0 個貓咪事件、0 個事件影片，符合負面測試。
- 本地合成 2 秒 testsrc + sine 音訊素材：組片後 FFprobe 確認同時有 video／audio。

### 2 小時壓力界線

已執行 **7,200 個邏輯影音秒（代表 2 小時時間軸）** 的分段輪替壓力測試，實際牆鐘約
46.994 秒；最後保留 61 個分段／61 秒、峰值約 62,464 bytes、孤兒 `.tmp` 0 個。
這證明輪替與容量邊界不會無限成長，但不是 2 小時持續 FFmpeg、網路來源或 MX250
實機證明；真正 2 小時連續程序仍為 `not_checked`。

## 6. 指標與尚未驗證項目

| 指標 | 本次結果 |
|---|---|
| 實際推論後端 | CPU（RTX 2060 開發機的 PyTorch CPU build）；不宣稱 GPU |
| GPU 是否真正使用 | 否；MX250 尚未實測 |
| 真實本地 FFmpeg 重啟次數 | 0（cat 短片正常結束） |
| watchdog forced-exit 測試 | fake process 重啟至少 1 次通過 |
| Telegram 真實成功／失敗 | 0／0（外部傳送未啟用） |
| fake Telegram 影片傳送 | 1 次成功；本地事件仍先完成 |
| AI 丟棄影格 | 本階段 cat 短片未記錄丟棄；無法推定 24H 數值 |
| 事件影片錄影完整性 | 本地含貓／音訊合成素材通過；RTSP／YouTube `not_checked` |
| 本地短測 SQLite 大小 | 約 98,304 bytes（不代表長期成長率） |
| 記憶體／CPU 平均值 | `not_checked`；本次未做長時間採樣 |
| 子程序數量 | 單一 `FfmpegMediaIngest` 輸入程序；未做 24H 監控 |

仍需分開完成：

1. 使用者提供並確認的 D-Link DCS-8526LH B1 RTSP 554 路徑、音訊與斷線恢復。
2. YouTube signed stream 解析、來源中斷及長時間重連。
3. 真實 Telegram Bot／多 Chat ID／檔案大小限制與無網路恢復。
4. MX250、8 GB／16 GB RAM 的 CPU／VRAM／溫度／記憶體採樣。
5. 實際 2～24 小時連續 FFmpeg 及家庭環境驗收。

在上述實機項目完成前，可以進入本地內部整合測試，但**不能宣稱已可對外發布或已通過
12～24 小時家庭環境驗收**。

## 6.1 2026-09-23 安裝包長 GOP 回歸

- 以 `%LOCALAPPDATA%\MeowKeeperPlus\App` 的 embedded Python、FFmpeg 與 YOLO11n，對
  `reports/phase3a-smoke/cat30.mp4` 執行 50 影格真實 CPU 推論；最高信心值約 0.92，偵測到
  2 隻貓。
- 真實素材只有一個 14.2 秒長 GOP MPEG-TS；舊索引在 writer 結束時把最後分段當成靠近結束
  時刻的 1 秒資料，較早事件因此找不到交集並降級成 JPEG。現在 ring session 保存開始時間，
  最後一個長 GOP 分段以 session wall-clock span 建立索引；實際 MP4 時長仍由 FFprobe 取得，
  不把估算值當成媒體時長。
- 安裝包驗收結果：SQLite 有 2 筆 `completed`／`telegram_status=disabled`，輸出 2 個 H.264
  640×480 MP4，各 478,184 bytes、14.2 秒、FFprobe 可讀；其餘 48 筆落在 5 秒事件合併窗，
  保存為 `merged_cooldown`，沒有建立大量重複影片。Telegram attempts=0。
- 此證據只驗證合法本地影片與 CPU；真實 RTSP／YouTube 長 GOP、Telegram 外傳、MX250 與
  12～24 小時家庭環境仍未驗證。

## 7. 2026-09-20 補充：真實 Xiaomi RTSP 後端組片

本節更新先前「RTSP `not_checked`」的範圍，保留 2026-08-02 原始驗收紀錄不變。

- 隔離 go2rtc v1.9.14 的 localhost RTSP 對 `chuangmi.camera.026c02`（已驗證後端 region）提供真實畫面；`FfmpegMediaIngest` 以單一輸入取得 45 張有效 JPEG、3 個已索引 MPEG-TS 分段。沒有建立第二條攝影機取流。
- FFmpeg 分段會等待關鍵幀，舊版以固定 1 秒推算每段時間，導致真實 `/live` 找不到最近片段。現以即時來源已完成分段的檔案修改時間索引，排除仍寫入的最新檔案；`test_realtime_ring_uses_completed_file_time_and_skips_writer` 驗證此規則。
- 真實來源的 `get_recent_clip(5)` 已輸出 FFprobe 可讀 MP4：5.998 秒、106,087 bytes。另以模擬事件建立 SQLite 紀錄與 MP4，`clip_status=completed`、`telegram_status=disabled`、schema version 3；這不是實際 YOLO 貓咪事件。
- 局部回歸：14 個相關 unittest 通過；修改的 Python 檔案 Ruff 與 compileall 通過。Telegram 對外傳送 0 次，暫時 stream 清理後為 0。私人影片與影格只在臨時目錄處理，未加入 repository。
- 仍未通過：已登入 Dashboard 預覽、真實含貓影格觸發的事件、Telegram 真實 Chat ID、`.56` 影格、MX250 與 12～24 小時穩定性。不得以本節推論整個 MVP 已可發布。

## 8. 2026-09-20 補充：無人值守重連與預覽生命週期

- RTSP／直播在 FFmpeg 乾淨 EOF 後仍需重新連線；一般 YouTube 影片有明確非直播 metadata 且正常播放完畢時才結束。預設網路 ingest 持續使用有上限延遲的退避重試；診斷時明確傳入的 retry policy 仍保留有限次數。
- 手動停止會設取消訊號並關閉舊 pipe；讀取中因 pipe 關閉產生的 `OSError`／`ValueError` 在確認取消後視為正常結束，不得誤標為來源故障或啟動新 FFmpeg。
- 控制器在停止、重啟或更換來源時清空預覽指標；受保護的 `/api/v1/preview/jpeg` 對上一個 session 的畫面回 404，避免使用者把舊畫面誤認為直播。
- fake-process 與 Dashboard API 局部測試涵蓋上述邊界；最新完整 unittest 178 項、全 repository Ruff、compileall 已通過。**尚未做真實網路斷線後 12–24 小時恢復實測**，不能僅憑模擬測試宣稱無人值守穩定性通過。

## 9. 2026-09-23 YouTube VP9 環形快取相容性

- 使用公開 YouTube 貓影片頁面（video id `I7ZRseJlZS4`）驗收時，yt-dlp 選到 360p VP9、無音訊。舊快取路徑把 VP9 stream-copy 到 MPEG-TS；雖然 JPEG／YOLO 正常，FFprobe 卻只看到 `bin_data`，事件降級為 `failed_with_snapshot`，沒有可用 MP4。
- `FfmpegMediaIngest` 現依 yt-dlp metadata 保留 H.264／H.265 MPEG-TS stream-copy；已知不相容的編碼改用低負載 H.264，輸出限制 640 寬、最高 5 FPS，GOP 依 `fps × segment_seconds` 對齊分段。未取得 codec metadata 時保留原 stream-copy 行為。
- 修正後，以 repository `.venv`、YOLO11n CPU、100 影格、360p 來源進行真實 YouTube 測試：偵測 1 隻貓（confidence 0.57），SQLite `media_events.clip_status=completed`，輸出 MP4 為 H.264 640×360、5 FPS、10.0 秒、447,760 bytes，FFprobe 可讀。Telegram 明確停用；signed media URL 標記掃描 0 命中。
- 驗證：Phase 2 事件影片 21 tests、YouTube adapter 6 tests、媒體 pipeline 12 tests 通過；變更檔案 Ruff 與 compileall 通過。此結果是一次短片驗收，不代表 YouTube 直播、RTSP、真實 Telegram、MX250 或 12–24 小時家庭驗收已通過。
- 此修正目前在 repository 原始碼；當前 `%LOCALAPPDATA%\MeowKeeperPlus\App` 尚未以此修正版重建／升級。

## 10. 2026-09-23 r2 可攜包與安裝驗證補充

- `ring_buffer.end_session()` 現在只清理該已結束 session 子目錄中的零位元組影音段（TS／M4S／MP4／MKV），避免空尾段成為永久孤兒；不清理 ring root 或其他 session 的檔案。新增回歸測試驗證範圍限制。
- 最終驗證共 200 個 unittest 通過；全 repository Ruff 與 `compileall` 通過。
- r2 Windows 可攜包 manifest 驗證通過：18,657 個檔案；模型 SHA-256 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`；`private_data_present=false`。
- r2 包內的真實公開 YouTube 測試以 100 影格、YOLO11n CPU 偵測到 1 隻貓（confidence 0.57）；SQLite 事件 `completed`，產生 FFprobe 可讀 H.264 640×360、5 FPS、7 秒 MP4（363,882 bytes）。Telegram disabled、attempts=0；signed URL 標記 0 命中，零位元組影音段 0 個。
- r2 已安裝至目前使用者 App 目錄；安裝後 `/healthz=200`、Host=1、Tray=1、go2rtc=0，Python 使用 embedded runtime。這不是 24H SYSTEM 啟動驗收；首次管理員設定及真實 Telegram 外送仍待完成。
