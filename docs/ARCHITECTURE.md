# 系統架構

總覽／即時畫面使用同一已登入文件與共享媒體控制器，切換展示容器不重新協商播放。其他頁面退出時清理媒體；來源收音與前端靜音分離，詳見 [共享播放契約](MONITOR_PLAYBACK_SESSION.md)。

CPU／RAM 與應用磁碟歷史使用獨立低頻背景採樣，不依賴瀏覽器或影音影格；
採樣與磁碟掃描失敗不停止監控。儲存、計量範圍與折線契約見 [RESOURCE_HISTORY.md](RESOURCE_HISTORY.md)。

## 1. 邊界與程序

```text
┌──────────────────────────────┐
│ MeowKeeperGUI（登入後）       │
│ 設定、預覽、圖表、日誌         │
└──────────────┬───────────────┘
               │ 本機 IPC（版本化契約）
┌──────────────▼───────────────┐
│ MeowKeeperService（24 小時）  │
│ 媒體、AI、事件、通知、資料庫    │
└──────────────────────────────┘
```

GUI 與背景服務必須是不同生命週期。V0.1 可由同一啟動器啟動兩者，但不得讓 GUI 物件持有正式監控核心。

## 2. 單一媒體入口

```text
RTSP / YouTube / VideoFile / Mock
           │
           ▼
    MediaIngest（單一入口）
      ├─ 壓縮封包 → RingBuffer → ClipAssembler
      ├─ 解碼影格 → PreviewSampler
      ├─ 低頻影格 → VisionPipeline（Noop／YOLO adapter）
      ├─ 音訊 PCM → AudioPipeline
      └─ 壓縮封包 → 選用 YouTube Relay
```

### AI 推論與來源影格解析度分離（2026-10-11，repository 已接線；正式 Host 尚待使用者升級）

單一 FFmpeg JPEG 分支依預覽、AI 與身分需求取最大寬度，最高 1920；只縮小、不放大來源。
基本 YOLO 與 V0.2 BoT-SORT 的推論尺寸可在 Dashboard 選 640／960／1280／1920，
預設仍為 MX250 保守基線 640。`imgsz` 不超過實際來源長邊；結果回報實際 inference size。
V0.2 使用同一個受監督 AI tracker process；行為／身分協調及 SQLite 留在 Host。
高解析度只提供更多來源像素，不保證辨識準確率提升。
V0.2 共用的高解析 JPEG 也保留至 `identity_frame_width`（預設 1280，640～1280 可設定），
最多 5 FPS；Ultralytics 回傳原始輸入影格座標，因此直接從共享影格裁切。
裁切仍須至少 96×96 且通過品質檢查，否則跳過身分，不阻塞偵測、快取或通知。

壓縮錄影優先 stream copy；來源編碼不支援而降級轉碼時，錄影分支仍限制最高寬 640。
設定頁「身分辨識保留畫面寬度」需保存後重啟服務套用。
這不會自動改攝影機 profile；來源只有 SD 時，應先使用已驗證的 HD profile。
高解析度 JPEG 會增加 CPU 縮放、編碼與短期記憶體成本，不能宣稱總負載完全不增加；MX250 尚待實測。

### 不變條件

- 每部攝影機只建立必要的單一 RTSP 連線。
- AI 可以降解析度、降 FPS，但這需要解碼與縮放運算。
- `-c copy` 只降低中繼推流的重編碼成本，不消除 AI 解碼成本。
- RingBuffer 優先保存約 1 秒的壓縮片段及索引。
- Stream-copy 的實際分段可能因關鍵幀間距超過 1 秒；FFmpeg 同時產生有界
  `segments.csv`（最多 4096 筆），索引以清單中的實際起迄差計算片長。即時來源的
  壁鐘結束時間仍是檔案 mtime 估計，不宣稱毫秒級攝影機時間同步；不完整清單延後索引。
- ClipAssembler 先嘗試重新封裝；需精確裁切或容量超限時才重新編碼。

## 2.1 MX250 效能 profile

自適應負載控制細節與狀態見 [ADAPTIVE_INFERENCE_DESIGN.md](ADAPTIVE_INFERENCE_DESIGN.md)。
repository 已將 JPEG reader 分離為執行緒、使用容量 1 latest-frame-wins 信箱，並將主要 YOLO／BoT-SORT 推論放到可逾時回收的子程序；過載可逐級降低尺寸與 FPS。40% 仍為軟目標（CPU 為 AI worker 正規化使用率，GPU 為整張裝置），不是硬體占用保證。進階尺寸與 process timeout 有本機真實模型／受控測試；MX250 與長時間硬體驗收仍待完成。詳見 [實作報告](ADAPTIVE_INFERENCE_IMPLEMENTATION_REPORT.md)。
640 是 MX250 保守基線，不是已證明所有 GPU 都不能以較高尺寸達到 5 FPS。

`mx250_minimum` 是產品最低設計契約，不是對任一模型的效能保證：

```text
系統 RAM：8 GiB 最低、16 GiB 建議
VRAM 預算：2 GiB
高頻視覺模型：同時 1 個；DINOv2 身分模型僅條件式低頻取樣
batch：1
模型輸入：640 像素級
AI 採樣：約 5 FPS
身分 embedding：條件式、最多約 1 FPS
行為彙整：約 1 Hz
後端順序：ONNX Runtime CUDA → CPU
```

GUI 預覽可使用獨立採樣率，但不得因此增加模型推論頻率。較多系統 RAM 只增加
媒體、GUI 與作業系統餘裕，不自動提高 batch、模型數量或 AI FPS。TensorRT、
姿態模型、多模型與高 FPS 延後到基線穩定後另行驗證。

## 3. 模組責任

### `app/gui`

- 舊版桌面原型的保留邊界；Phase 3 正式操作介面改為 Web Dashboard，不再新增大型
  CustomTkinter／PyQt 主介面。
- 若保留相容元件，仍不得直接呼叫攝影機、Telegram 或資料庫。

### `app/service`

- 監控生命週期、健康狀態與有序關閉。
- 協調媒體、AI、事件、通知與儲存模組。

### `app/ipc`

- GUI 與服務的版本化請求／事件契約。
- 初版可評估 localhost HTTP、Named Pipe 或 multiprocessing connection。
- 技術選擇於 V0.1 以 Windows 權限、取消語意和除錯便利性決定。

### `core/media`

- `MediaSource` 介面與 RTSP、YouTube、影片檔、Mock 實作。
- YouTube 正式來源使用固定版 `yt-dlp`／`yt-dlp-ejs`；`YouTubeSourceAdapter` 以
  `max_height` 選取格式、回報 metadata，並在 FFmpeg 結束後重新解析短期 URL。舊
  `streamlink_path` 僅保留給相容測試，不是正式入口。`FfmpegMediaIngest` 仍以一個
  FFmpeg 輸入程序同時輸出低 FPS JPEG 與壓縮 MPEG-TS 分段，實際來源 URL 不寫入日誌。
- `DiskSegmentRingBuffer`、`SegmentIndex`、`ClipAssembler`、`EventClipService` 分離
  分段輪替、引用保護、MP4 組片／驗證、`/live` 與事件佇列。
- `SegmentRecorder` 是可獨立 watchdog 的分段器；RTSP 沒有本地 ingest 時會拒絕啟動，
  不建立第二條攝影機連線。
- 音訊只保留在壓縮分段；JPEG 影格不含音訊，僅供即時 AI／預覽。

### `core/detection`

- 可替換的偵測後端介面。
- V0.1 提供本地 YOLO 影格分析適配器；預設使用已驗證的 `models/yolo11n.pt`。
  開發／建置階段由 `tools/fetch_model.py` 從固定的官方 `ultralytics/assets` release
  取得權重並產生 `models/model_manifest.json`；啟動時核對來源欄位、檔案大小與 SHA-256。
- V0.1 使用 COCO `cat` class id `15`，預設信心門檻為 `0.45` 且可由設定覆寫；其他
  類別在通知前丟棄。
- `.pt` 權重目前經 Ultralytics PyTorch 適配器執行；實際後端須回報 `PyTorch CUDA` 或
  `CPU`。MX250 產品基線仍優先評估 ONNX Runtime CUDA，失敗則降級 CPU，不以 RTX 2060
  結果作為 MX250 效能證明。

### `core/tracking`

- 短期追蹤與軌跡；追蹤 ID 不等於永久身分。

### `core/identity`

- `PetRegistry` 保存任意使用者登錄的寵物與參考影像。
- 官方 DINOv2 ViT-S/14 為低頻、條件式 embedding 後端；多個 L2 向量與 HSV 色彩特徵融合，
  門檻不足回傳 `unknown`。`TemporalIdentity` 只在短期 track 內投票。
- PetFace 僅保留未來介面，不作 V0.2 必要依賴；不把固定寵物名稱寫入程式。

### `core/behavior`

- `ZoneRegistry`、`RuleBehaviorClassifier`、`BehaviorStateMachine` 與 `BehaviorEventMerger`
  將物件、區域、動作與時間規則融合成三維狀態。
- `running` 只屬 `motion_state`，不會自動轉成 `playing`；R3D18 為可選、需核准 checkpoint
  的時序介面，預設仍用規則。
- 狀態契約定義於 `src/meowkeeper/domain/behavior.py`。

### `core/events`

- 事件合併、冷卻、前後錄請求、人工確認與告警升級。

### `core/notifications`

- Telegram Bot 指令、Inline Keyboard、權限與通知重試。
- 第一版一個 Bot、多 Chat ID。
- 分析事件可透過 Bot API `sendPhoto` 傳送 JPEG；只有明確啟用且通過 Chat ID
  權限後才會送出。
- 事件 MP4 使用 `sendVideo`，由獨立有界佇列、逾時、有限重試與退避送出；Telegram
  失敗不會刪除本地影片或停止媒體 ingest。送出前以 FFprobe 驗證影片：H.264、
  `yuv420p`、有效寬高、可讀時長，以及 MP4 `moov` 位於 `mdat` 前時才直接上傳；
  HEVC/H.265、其他像素格式、索引不合格或超過大小上限時，僅在通知佇列中產生
  H.264/AAC、`yuv420p`、`+faststart` 的暫時副本。原始事件影片保留，轉碼失敗則改送
  截圖與錯誤狀態。

### `core/camera`

- ONVIF 探索、唯讀能力與明確授權的 Preset 操作。

### `core/analytics`

- 行為區間彙整、有效監控時間、日／週／月報。

### `storage`

- SQLite repositories、遷移與交易；V0.2 表包含 pets、pet_embeddings、tracks、
  behavior_events、media_events、event_labels、zones、model_registry、identity_corrections。
- 只在 track 開始、行為狀態變更、事件完成或批次邊界寫入，不逐影格寫入。
- 秘密只保存 secret reference，不保存秘密明文。

## 4. 核心資料流

```text
MediaSource
  → timestamped media
  → detection / audio observations
  → tracking + low-cost behavior inference
  → temporal smoothing
  → behavior intervals + event candidates
  → event persistence / notification
  → RingBuffer clip
  → Telegram
  → background identity resolution
  → identity patch / statistics
  → SQLite
```

每一階段保留 `source_id`、時間戳、模型／規則版本與信心值，讓錯誤可追溯。

V0.2 協調器 `V02AnalysisPipeline` 只接收既有 `FramePacket`，並依序呼叫同一個
`TrackingBackend`、身分、行為與儲存模組；它不會自行建立第二個 RTSP 來源。

## 5. 行為資料

- `behavior_state`：互斥主要行為。
- `motion_state`：可與主要行為並存。
- `event_tags`：同一時間可多個。

例如：

```json
{
  "pet_id": "01J...",
  "behavior_state": "playing",
  "motion_state": "running",
  "event_tags": ["chasing", "cat_interaction"],
  "raw_confidence": 0.82,
  "calibrated_confidence": null,
  "model_version": "not-selected",
  "camera_preset": "living-room-view"
}
```

`calibrated_confidence: null` 表示尚未完成校正，不能把 0.82 稱為 82% 真實準確率。

## 6. 設定與秘密

```text
config.json
├─ 媒體秒數、門檻、區域、Chat ID metadata、權限
├─ 寵物設定與非敏感 metadata
└─ secret_id（索引）

Windows Credential Manager / DPAPI
├─ Telegram Bot Token
├─ RTSP 密碼
├─ 攝影機 PIN
└─ YouTube Stream Key
```

設定具 `schema_version`。未知欄位應保留或明確遷移，不靜默遺失使用者設定。

完整資料備份由 Dashboard 產生密碼保護的 ZIP。外層只放不含秘密的 manifest，
設定、SQLite 一致快照、`data/state/telegram-cat-notifications.json`、`data/pets`、
參考影像、embeddings 與已完成事件檔案放在 AES-GCM 加密 payload；ring／preview 暫存、
模型、FFmpeg 與 Python runtime 不備份。匯入先驗證密碼、路徑與 SHA-256，再寫入資料並要求
安全重啟 Host。

Dashboard 的 AI／行為頁只寫入非秘密 `dashboard_settings`（信心門檻、受 MX250 範圍限制的
採樣 FPS、冷卻、事件前後錄與壓縮 ring retention／容量）。Host 啟動時載入並交給
`RepositoryCoreController`；保存不會在執行中的 ingest 上重建 FFmpeg，需安全重啟後套用。
Telegram `enabled` 是非秘密開關；Bot Token／Chat ID 可由已登入 Dashboard 保存至
Windows Credential Manager（Credential Manager 不可用時使用 DPAPI），前端只顯示是否已設定
與保存方式，不回傳秘密原值。環境變數仍是可攜式包與診斷模式的 fallback。

## 7. 可靠性

- 網路瞬斷：有上限指數退避加 jitter。
- 認證失敗：停止快速重試並提示修正憑證。
- FFmpeg 子程序退出：捕捉 stderr 摘要、遮罩秘密、有限次重啟。
- 發布 runtime 優先使用 `vendor/ffmpeg/bin` 內含的 `ffmpeg.exe`／`ffprobe.exe`，
  不要求使用者另外安裝全域 FFmpeg。
- GPU 記憶體不足：降級採樣或後端，並記錄能力變更。
- 16 GiB RAM 不解除 MX250 的單模型、batch 1 與 5 FPS 保守預設。
- 磁碟不足：停用一般事件保存，異常警報仍可傳文字或快照。
- GUI 離線：服務持續執行，重連後補送狀態。

## 8. 部署演進

- Phase 0：命令列唯讀探針。
- V0.1：可視桌面原型，服務與 GUI 邏輯分層。
- V0.2：壓縮環形快取與 Telegram。
- V1.0：Windows 背景服務、IPC、安裝、升級與復原。

Windows Task Scheduler 在「無人登入」時不能顯示 GUI，因此背景服務與互動介面不可合併為單一視窗程序。

## 10. Phase 3 Web 與 Tray 邊界

Phase 3 的操作層是 headless Core、FastAPI、server-rendered HTML/Jinja2、原生 JavaScript/CSS
與可選 pystray。Host 以 `RepositoryCoreController` 接上既有 Phase 2 元件，Dashboard 與 Tray 只使用 `CoreStateFacade` 和 `/api/v1`；不得直接建立
FFmpeg、YOLO、Telegram 或 SQLite 業務連線。預設 server bind 為 `127.0.0.1:8000`，關閉
瀏覽器或 Tray 不會停止核心。即時畫面只可使用既有 ingest JPEG、未來本地 restream 或
go2rtc，不得因頁面開啟而新增攝影機 RTSP 連線。事件列表以 SQLite 分頁；事件影片以授權
API 路徑檢查後提供，不掛載事件資料夾。

即時畫面下方的解析度與 FPS 取自同一個 ingest JPEG／執行中狀態：解析度由最新 JPEG
標頭讀取，FPS 是 Host 影格迴圈的實測值；尚未收到影格時顯示「尚未取得」，不得以
攝影機宣稱值冒充實測結果。

Tailscale 與 Xiaomi/go2rtc 是外部整合邊界。Tailscale 由官方 Windows Client／CLI 管理，
MeowKeeper+ 不實作 VPN、不記錄 Auth Key、不預設 Funnel；Xiaomi 只在使用者確認機型、
區域與韌體後，以官方 go2rtc localhost bridge 進行探測。兩者未經實機資料驗證一律標示
`not_checked` 或 `experimental_not_verified`。

Dashboard 的 Tailscale 頁面只呼叫官方 CLI 的唯讀 `status --json`／`serve status --json`，
並將安全摘要提供給受保護 API；Python 不執行登入、`tailscale up`、Serve 或 Funnel。Host
可在健康檢查成功後，透過既有有界 Telegram outbound dispatcher 選擇性送出啟動摘要。LAN
網址由目前啟用實體介面動態計算，排除 loopback、link-local、Tailscale 100.64.0.0/10
與虛擬網卡；wildcard bind `0.0.0.0` 永遠不會直接出現在可分享 URL。

Dashboard 遠端裝置與觀看者狀態另以唯讀 peer 清單及 10 秒 single-flight cache 提供，並用
短效、記憶體內的 session／播放器活動紀錄區分裝置 online、Tailscale active、登入 Dashboard
與實際 JPEG／MSE／WebRTC 觀看。觀看者只由成功送出的影格／媒體及已驗證 session 計入；
socket peer 必須與官方 CLI peer IP 相符才配對裝置，不信任 forwarded headers。詳細契約及
not_checked 範圍見 [TAILSCALE_INTEGRATION.md](TAILSCALE_INTEGRATION.md)。

## 9. V0.2B 行為優先順序與負載隔離

主路徑固定為：

```text
Priority 0 影音接收／壓縮環形快取（不可被 AI 阻塞）
    ↓
Priority 1 YOLO／BoT-SORT／低成本動作與行為特徵
    ↓
Priority 2 行為事件落地、片段請求與必要 Telegram 通知
    ↓
Priority 3 DINOv2／色彩身分解析（背景、有界、可回傳 unknown）
    ↓
Priority 4 日報、熱力圖與離線資料整理
```

`GPUInferenceScheduler` 使用單一工作者、優先佇列與去重鍵；GPU 忙碌時先淘汰過期
的 LOW／BACKGROUND 工作，不淘汰已建立的行為事件。身分尚未完成時，事件以
`pet_id = null`、`identity_status = unknown` 保存，解析完成後更新同一 `event_id`，
並在 `identity_corrections` 保留原始與修正稽核。

Telegram 送出使用有界背景佇列；網路延遲不會阻塞追蹤或行為分析。AI 影格可採
latest-frame-wins 略過中間影格，但壓縮影音環形快取不可略過。

`CompressedRingBuffer` 只保存壓縮片段，`LatestFrameQueue` 只服務 AI 採樣；兩者不可
共用同一個會因 GPU 延遲而無限增長的原始影格佇列。

`InferenceLoadController` 以一般 3 FPS、活動 5 FPS、安靜 1 FPS 為起點；貓進入畫面、
速度提高、重點區域或異常候選會進入可設定的活動增強窗口（預設 30 秒）。GPU
`THROTTLED` 時降至 1～2 FPS 並延後 DINO；`CRITICAL` 時停止低優先模型，保留媒體、
環形錄影與通知。所有實際後端與降級狀態都必須報告，不以開發機推測 MX250 效能。

## 10. MVP 穩定性邊界

`FfmpegFrameSource` 對網路來源使用有限指數退避與 FFmpeg 子程序重啟；YouTube adapter 使用
3、6、12、24、60 秒退避並重新執行固定版 yt-dlp，不猜測 signed URL。YouTube 媒體由
一個固定版 yt-dlp upstream 以 finite HTTP chunks 寫入匿名 pipe，再由唯一 FFmpeg 程序
解碼並同時輸出分析 JPEG 與壓縮 ring；RTSP 仍由 FFmpeg 直接讀取，兩者都不為預覽另開
來源連線。`CompressedRingBuffer` 與 `LatestFrameQueue` 是
壓縮片段／最新影格的分離契約；在尚未接入完整事件片段組裝前，不得把契約寫成已完成 MP4
環形錄影。

SQLite 使用 schema version、可重跑欄位 migration、WAL、busy timeout 與有界 locked/busy
重試。日誌輪替、磁碟空間檢查與明確事件目錄保留工具位於 `core/health`；正式服務接線與
長時間數據仍需 Release Gate 實機驗收。

GPU、DINOv2 或其他 AI 錯誤只能使該次推論降級／保留 `unknown`；不得停止媒體接收、事件
保存或 Telegram 狀態。所有 runtime 與 FFmpeg 的終端使用者依賴應在 Windows 可攜式包內
提供，建置規則見 `docs/BUILD_WINDOWS.md`。

## 11. Phase 3B 啟動器、Tray 與 go2rtc 邊界

Windows 啟動器由 `tools/launcher.py` 統一負責 runtime 檢查、官方 go2rtc 啟動／重用、Host
健康檢查、瀏覽器開啟與可選的 Tray 啟動。`Start-MeowKeeperPlus.cmd` 與
`Start-MeowKeeperPlus-Tray.cmd` 只負責以 PowerShell 呼叫啟動器；它們不得自行建立第二個
Host、go2rtc 或要求使用者手動建立 venv。
開發期若 `.venv` 缺少專案宣告的 runtime extras，啟動器可在 repository-local 環境執行受控
bootstrap；正式 Windows 可攜式包則應使用內含 runtime，不依賴使用者的 Python 或 pip。

Tray 是 localhost client，不負責停止 Core；它透過受保護的 Web API 查詢狀態、開啟 Dashboard、
顯示通知並維持單一實例。Tray 圖示、通知與視覺驗收屬於 Windows 桌面環境驗收項目，沒有
通知區截圖時不得宣稱已完成完整視覺驗收。

go2rtc 僅使用官方 GitHub release 的固定版本與 SHA-256 manifest，API 固定綁定 localhost，並由
`Go2RtcProcess` 管理啟動、停止、健康檢查、port owner、跨程序 lock、有限 watchdog 與 log
rotation。正式 1984／8554 可唯讀重用既有相符的 AppData session config；非正式 port 使用
repository-local config，避免測試覆寫登入 session。小米設定服務只
提供本地設定精靈、設備／profile 探測及單一本地來源切換契約；未完成使用者登入、驗證碼或
串流品質驗收時，狀態必須維持 `waiting_for_login`、`not_checked`、`degraded` 或
`experimental_not_verified`，不得猜測帳號、密碼、區域或設備 URL。

`tools/run_host.py` 預設使用 `1984/8554`；隔離的官方 go2rtc session 可透過
`--go2rtc-api-port`、`--go2rtc-rtsp-port` 或對應環境變數注入 localhost port。這些值會
  同時傳給 API client、裝置 profile probe、受控 stream 與唯一媒體來源，避免 Dashboard
讀取一個 port 而 FFprobe／RTSP ingest 讀取另一個 port；預設正式路徑不變。只有設定
`go2rtc.rtsp_lan_access_enabled=true` 且 Private／localsubnet 防火牆規則已確認時，RTSP
才綁 `0.0.0.0:8554`；1984 API 永遠不對區網開放。

### 即時預覽與明確截圖

瀏覽器即時預覽共用唯一 `FfmpegMediaIngest` 與現有 go2rtc producer，不建立第二條攝影機
RTSP 或 FFmpeg producer。首頁預設「節能」JPEG，最高每秒輪詢 5 次；即時頁預設嘗試 MSE，
由登入保護的同源 WebSocket 代理到本機 go2rtc `/api/ws`，只使用 Core 回報的活躍
`xiaomi_camera_*` stream，最多 4 個瀏覽器消費端。來源不可用、瀏覽器不支援或 10 秒內
沒有解碼影格時，明確退回 JPEG。畫面「休眠」會停止該頁 JPEG／MSE 與狀態請求；不停止
監控、AI 或事件錄影。連續播放實際 FPS／接收速率由瀏覽器呈現與收到的位元組估算，
不能替代攝影機實際錄影規格；實體 HEVC 瀏覽器相容性與多觀看者硬體負載需另做實機驗收。

使用者明確按下截圖或 Telegram `/snapshot` 時，
本機 Xiaomi 來源會改向同一個 go2rtc producer 的 `/api/frame.jpeg` 取得原生解析度 JPEG；
不建立第二條 RTSP、FFmpeg 或攝影機連線。若原生 frame API 不可用，才回退到最近一張
ingest JPEG，並在狀態中標示 `snapshot.source=ingest_preview`，不得宣稱已取得硬體最高解析度。

### Xiaomi MISS PTZ 實驗邊界

PTZ 控制不是影音 ingest 的必要路徑。只有明確啟用隔離實驗 go2rtc 後，Dashboard 才能透過
登入保護的本機代理讀取或送出 allow-listed MISS PTZ 指令；它只使用已建立的單一 Xiaomi
producer，不建立第二條來源連線。官方 v1.9.14 與正式發布 binary 不因 PTZ 接線而替換，
方向、停止、位置與校準的實驗規格見 `docs/XIAOMI_MISS_PTZ_EXPERIMENT.md`。

## Xiaomi 登入診斷邊界

`Go2RtcClient.get_diagnostic()` 僅對 localhost `/api/xiaomi` 做一次 read-only response capture；
`XiaomiSetupService.capture_go2rtc_login_diagnostic()` 再交由分類器與冷卻狀態機處理。登入流程
若由明確受控呼叫端送出，POST 與登入後 GET 必須分開處理；POST 200 空 body 只代表請求已接受，
GET 200 帳號清單再驗證 session。`Go2RtcClient.post_xiaomi_login()` 僅將欄位放入 body，
不自動重試或保存秘密。一般登入仍由 go2rtc 官方本機頁面完成，MeowKeeper+ 不代送密碼、CAPTCHA、驗證碼或 Token。response body
與 go2rtc 日誌在寫入前即遮罩；`70016 + captchaUrl=null` 不會被當成帳密錯誤，也不會觸發
自動重試或提前進入 devices stage。

## Dashboard 來源與最小 MVP 鏈路

`/settings/source` 是來源與攝影機的單一入口。`SourceConfigService` 只保存 YouTube
頁面網址、RTSP 不含帳密的 URL 或本機測試影片路徑；signed YouTube media URL 永不進入
設定、SQLite 或日誌。啟動時 Host 依序讀取 CLI／來源環境變數／`config.json.media`，
並由一個 `RepositoryCoreController` 建立一個 `FfmpegMediaIngest`。

```text
YouTube／RTSP／本機影片
        ↓
單一 FfmpegMediaIngest
  ├─ 最新 JPEG → YOLO11n cat-only（COCO class 15）
  ├─ 壓縮 MPEG-TS ring buffer → 事件 MP4
  └─ EventClipService → SQLite → Telegram Bot API 背景佇列
```

Telegram 啟用狀態是非秘密設定；Bot Token 與 Chat ID 可由頁面保存到本機安全憑證邊界，
頁面明確啟用後需重啟 Host。通知是 outbound Bot API `sendVideo`／`sendPhoto`；真正 Telegram
webhook 需要外部 HTTPS／公開入口，未在 localhost MVP 中假裝已完成。
