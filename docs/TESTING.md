# 測試策略

## Tailscale 裝置與觀看者狀態

`tests/test_tailscale_viewers.py` 覆蓋官方 `status --json` 解析與部分回應、穩定且不洩漏
節點金鑰的裝置參照、本機別名保存、10 秒共用快取、session／JPEG／MSE／WebRTC 活動去重、
登入／登出與過期清理、未知／代理來源不冒認裝置，以及受保護 API 與 CSRF。MSE 播放器只在
收到媒體資料後計入；RTSP／影格來源的 producer 數不是觀看人數。

隔離測試不證明 Tailscale 真實登入、兩地網路可達、direct／relay 路徑、瀏覽器真實播放或
正式 Host 部署。這些必須使用實際兩端設備另行驗收，沒有實際環境時記為 `not_checked`。

## 2026-10-17 正式產品驗收路由

近期需求與接線／驗收界線見 [PRODUCTIZATION_SPEC.md](PRODUCTIZATION_SPEC.md)，當前任務見 [CURRENT_GOALS.md](CURRENT_GOALS.md)。正式1.0不能只以unit tests或包雜湊決定；需依整合規格的P0–P5完成runtime、交付、資料復原、長期可靠性、安全與新手任務驗收。

每份驗收至少記錄code／package／installed／running版本、資料根、來源與硬體、操作、預期／實際、passed/failed/not_checked及遮罩證據；模擬座標不證明真實PTZ，合成圖片不證明家庭辨識率，API送出不證明Telegram收件與回報落庫。

建議新增發布矩陣包含：Windows10/11、可攜／CurrentUser／SYSTEM、首次／升級／重開機／解除安裝、跨版本加密備份復原、Public/Private防火牆、無管理員、CPU/Intel/NVIDIA、休眠／節能／流暢、兩觀看者單一上游、斷網／滿磁碟／AI失敗與程序占用競態。這是待執行門檻，不代表目前CI已全部覆蓋。

本次文件整合只查內容、入口與本機連結，未重跑歷史全測、建置或部署。

## 1. 原則

- 無實機、無網路、無 Token 也能執行單元測試。
- RTSP、Telegram、FFmpeg、時間與檔案系統皆須可替換或 Mock。
- 實機測試與單元測試分離，不在 CI 自動控制 PTZ。
- 測試資料不得含真實居家畫面或秘密。

## 2. 目前最小測試

```powershell
python -m unittest discover -s tests -v
```

目前驗證：

- 行為列舉與文件契約一致。
- 主要行為、動作狀態與事件標籤互相分離。
- 身分裁切共享影格可保留最高寬 1280，YOLO 明確以 640 推論；原圖座標裁切不得縮小或錯位。
- 真實 FFmpeg 以合成 1920×1080 素材產生 1280×720 JPEG，以 640×360 素材仍產生 640×360，不假放大。
- `identity_frame_width` 經 Web 驗證、config 保存及 Host 載入一致；範圍 640～1280 整數。
- 快取設定拒絕預錄大於總快取。
- RTSP URI 遮罩不洩漏帳密。
- 探針缺少選用資料時回報 `not_checked`，而非崩潰。
- YouTube adapter 可在 mock 外部程序下取得 metadata；實際 signed URL 不進入設定、日誌或
  狀態 JSON，並有固定版本／FFmpeg／FFprobe／JavaScript runtime／yt-dlp-ejs 探針。
- FFmpeg frame source 對 RTSP 使用 TCP、以單一程序輸出 JPEG，並支援本地影片乾跑。
- `models/model_manifest.json` 與 `models/yolo11n.pt` 的來源、大小與 SHA-256 一致；缺少
  或損毀時回報繁體中文錯誤。
- YOLO 真實載入 COCO 預訓練權重，以含貓圖片找出 class id 15；非貓圖片不產生貓咪事件。
- Telegram 關閉或沒有 notifier 時，貓咪事件不會送出外部訊息。
- `/catnotify off` 與 `/mute N` 須按獲授權私人 Chat ID 各自生效，在實際發送前重新檢查；
  靜音時仍保存 SQLite 事件與本地 MP4，偏好經 Host 重啟可讀回，N 小時到期自動恢復。
  未授權 Chat ID 不得改變任何通知偏好；單元測試不對真實 Bot 發送。
- `frame_analysis.py` 回報實際 `PyTorch CUDA` 或 `CPU`；ONNX Runtime provider 存在不等於
  本次 `.pt` 推論已使用 CUDA。
- 分析事件可對多個 Chat ID 傳送 JPEG，冷卻期間不重複傳送。
- MX250 profile 固定為單一模型、batch 1、640 像素級輸入及不高於 5 FPS。
- 8 GiB RAM 通過最低值，低於 8 GiB 警告；16 GiB 不自動提高 GPU 負載。
- 統一 Phase 0 入口不會要求 Telegram 傳送測試訊息。
- V0.2 需驗證任意寵物登錄、DINOv2 權重 manifest、unknown 與時間投票、BoT-SORT `persist`
  與 `with_reid` 設定、running 不等於 playing、區域規則與用餐合併、音視候選與 SQLite 表。
- V0.2 協調器只能使用一個 `FramePacket`／追蹤器入口；測試應對多次更新檢查沒有第二條
  RTSP 連線或逐影格資料庫寫入。
- V0.2B 必須驗證行為事件可在身分延遲時先以 unknown 落地，背景結果可補寫同一 `event_id`，
  且 `identity_corrections` 保留稽核紀錄。
- V0.2B 排程器必須驗證 HIGH 行為工作優先於 LOW DINO、同一 track 去重、低優先佇列有界、
  GPU THROTTLED／CRITICAL 時低優先工作延後、Telegram 背景送出不阻塞主路徑。
- MVP Phase 2 必須驗證 `DiskSegmentRingBuffer` 的時間／容量輪替、事件引用保護、
  `FfmpegMediaIngest` 的單一輸入與壓縮分段、前 5 秒＋後 10 秒事件 MP4、`/live` 前 5 秒、
  無音訊／有音訊、stream copy→轉碼降級、FFmpeg watchdog、Telegram disabled 與 SQLite
  `media_events` 狀態更新。
- Telegram 影片相容性必須分別驗證：H.264 + `yuv420p` + `moov` 前置時不重複轉碼；
  HEVC/H.265、非 `yuv420p` 或索引不合格時才建立 H.264/AAC 暫時副本，且不得覆寫原始事件影片。

## 3. 測試金字塔

Telegram 每日摘要的取樣持久化驗證：
`tests/test_daily_monitoring.py`、`tests/test_telegram_commands.py` 與
`tests/test_phase3a_bringup.py`。需覆蓋 schema migration 可重跑、null／0 區分、
跨日、來源中斷、SQLite 鎖定不阻塞影格、有界背景 writer、正常關閉與本地 FFmpeg
影片 JPEG／統計讀回。不將本地 Noop 影片測試稱為真實相機或貓咪活動準確度驗收。

### V0.1 真實模型驗收

`tests/assets/cat.jpg` 是公開 COCO 驗證影像（含兩隻貓），`tests/assets/bus.jpg` 是官方
Ultralytics 公開非貓範例。`tests/test_real_yolo.py` 直接載入 `models/yolo11n.pt`，不使用
MockDetector，並以 CPU 執行可重現的真實推論。這些測試不設定 Telegram Token，也不呼叫
Telegram 網路。

本機影片乾跑命令：

```powershell
python tools\run_monitor.py --source-url tests\assets\bus.jpg --analyzer yolo --max-frames 1

V0.2 乾跑（不登錄寵物時身分自動停用，不送 Telegram）：

```powershell
python tools\run_v02.py --source-url <local-video> --disable-identity --max-frames 10
```

完成寵物登錄後再省略 `--disable-identity`，會使用已驗證的官方 DINOv2 checkpoint；
輸出會列出身分 backend、影格數、掉幀數與 embedding latency。
```

實際串流測試仍需另行提供 YouTube/RTSP 來源；未提供時維持 `not_checked`，不猜測 URL。

### 單元測試

- 設定驗證、權限、狀態轉換、事件合併、冷卻、統計分母與秘密遮罩。
- 完整資料備份以至少 6 字元密碼建立 ZIP，驗證 SQLite snapshot、Telegram 偏好、寵物資料、
  事件檔案可往返還原；錯誤密碼、竄改 payload 與不安全路徑不得寫入資料。
- Mock 外部程序與網路。

### 元件測試

- 使用測試影片驗證時間戳、環形快取、前後錄與重新封裝。
- 使用錄製的無敏感 metadata 測試 FFprobe parser。
- 使用 Mock Telegram server 驗證權限與重試。

### 本機整合測試

- `FfmpegFrameSource` 加 `NoopFrameAnalyzer` 跑測試影片；不要在單元測試傳送真實 Telegram。
- SQLite 暫存資料庫。
- GUI 連本機 Mock service。

### 實機測試

- 使用獨立標記執行，需明確的環境變數與使用者同意。
- 預設不傳 Telegram 訊息、不移動 PTZ。
- 任何 PTZ 測試先顯示將呼叫的 Preset，並確認鏡頭周圍安全。

### 長時間測試

- 24、72 小時串流穩定性。
- Wi-Fi 中斷、攝影機重啟、Token 失效、FFmpeg 退出、GPU OOM 與磁碟不足。
- 驗證服務可恢復且不造成通知風暴。

## 4. 測試資料來源

- `SyntheticSource`：純程式生成時間戳與狀態。
- `VideoFileSource`：使用者主動放入未追蹤的本地測試影片。
- 開源資料：使用前確認授權與隱私。
- 真實事件：預設不提交 Git；人工確認後仍需本地保存。

## 5. 行為驗證

行為模型不可只看整體準確率。至少分別觀察：

- 每類 precision、recall 與誤報頻率。
- 每隻寵物、每個攝影機 Preset、白天／夜間。
- 行為區間的開始／結束誤差。
- 個體 ID 交換率。
- `out_of_view` 和 `unknown` 被誤算成睡眠的次數。
- 模型版本與規則 profile 的回歸差異。

Telegram 人工確認是標註來源，不直接當成無偏真值；「無法判斷」必須保留。

## 6. 完成門檻

每個版本需在 Roadmap 驗收之外：

- 單元測試通過。
- User-facing 文字為繁體中文。
- 秘密掃描沒有命中真實憑證。
- 模擬來源可走完主要流程。
- 實機相關結論附當次測試條件，不把未檢測寫成不支援。

V0.2B 乾跑與報告：

```powershell
python tools\gpu_probe.py --help
python tools\run_v02.py --source-url <local-video> --disable-identity --max-frames 10
```

30 分鐘以上影片壓力測試若未執行，報告必須明確標示 `not_checked`，不可以單元測試
代替 24 小時穩定性證明。

MVP Phase 2 本地事件影片乾跑：

```powershell
python tools\run_monitor.py --source-url reports\cat_test.mp4 --analyzer yolo --event-video --device cpu --max-frames 5
```

上述命令只在加上 `--send-telegram` 並提供 Token／Chat ID 時才會啟用外部影片傳送。

## 7. MVP Release Gate 驗收分類

每個需求均須附程式碼／測試／實機證據，並使用以下狀態之一：

- `implemented`：主流程已接線，且有不依賴私人設備的測試。
- `interface_only`：有契約或工具，但尚未接入 24H 主服務。
- `mock_only`：目前只有 mock／synthetic 測試。
- `not_implemented`：尚無可執行實作。
- `needs_real_data`：需要家庭攝影機、網路、GPU 或 Telegram 實機資料。
- `not_checked`：本次沒有足夠時間或設備執行；不可用短測替代。

Release Gate 必須另列：實際推論後端、CPU／記憶體、GPU 使用、FFmpeg 重啟、Telegram
成功／失敗、AI 丟幀、錄影完整性、SQLite 大小、磁碟成長與壓力時數。若 2 小時以上自動
壓力測試、RTSP／YouTube 中斷恢復或實機通知未執行，報告要保留 `not_checked`，不得宣稱
已可對外發布。

Windows 發布測試另外確認：包內有 Python runtime、必要套件、FFmpeg、預設模型與 manifest；
包內不得含秘密或私人 SQLite。終端使用者測試不應要求 venv、pip 或手動下載任何檔案。
# Phase 3 Web／Tray 測試補充

Phase 3A 的 Web／Tray runtime 必須在專案 `.venv` 由開發／建置者安裝；不得修改系統
Python、驅動或 CUDA。安裝 `.[web,tray]` 後，Web smoke 不得再以「依賴未安裝」跳過：

```powershell
python tools\run_web.py --help
python tools\run_host.py --no-open-browser --help
python tools\run_tray.py --help
python -m unittest tests.test_phase3_web_tray -v
```

驗收重點：未登入不能讀取事件／修改設定；設定範圍與危險 command 欄位被拒絕；事件列表
使用 LIMIT/OFFSET；事件影片路徑越界被拒絕；SSE 佇列有界；Tray 只呼叫 localhost；關閉
Dashboard／Tray 不呼叫核心停止；Tailscale 不開 Funnel；小米秘密不進公開資料。

## Xiaomi 70016 登入診斷

```powershell
python -m unittest tests.test_xiaomi_login_diagnostics -v
```

測試必須確認 70016 且 `captchaUrl=null` 不分類為 `invalid_credentials`、HTTP／Content-Type／
body／日誌會被安全擷取、LoginError 只保存 presence、冷卻期間沒有自動重試，並且只有明確
重試 API 能解除登入 gate。正式實機驗證仍需使用者在官方本機頁面完成帳號登入；測試不可要求
或提交任何密碼、驗證碼或 Token。

## Phase 3E 區網 Dashboard

- `tests.test_phase3e_lan_access` 驗證 localhost 預設、明確 LAN wildcard bind、設定檔原子保存、
  私有 IPv4 過濾、同源前端 URL、受保護網路／媒體 API 與 Private-only 防火牆命令。
- 需要 Windows 真實網卡與系統管理員權限的防火牆規則、iPhone Safari、同一區網跨裝置登入，
  以及一台／兩台瀏覽器的長時間 CPU／RAM 觀測，必須另以實機報告標示 `not_checked` 或
  `needs_real_data`；單元測試不得代替這些證據。

## Dashboard 最小影音／通知鏈路

`tests.test_phase3_source_mvp` 驗證來源設定只保存 YouTube 頁面 URL、拒絕 RTSP URL
userinfo、`/cameras` 導向合併後的 `/settings/source`，以及保存設定會呼叫核心來源
callback。真實本地乾跑使用 `reports/phase3a-smoke/cat30.mp4`、預設 `yolo11n.pt`、
CPU，驗證取得 cat 事件、事件 MP4 與 SQLite 紀錄；公開 YouTube smoke 使用固定 yt-dlp
解析並取得第一個來源影格。D-Link RTSP、家庭 YouTube 直播與 Telegram 真實 Chat ID
仍必須另行記錄 `needs_real_data`，不得由本地影片或 mock 取代。

## Xiaomi MISS PTZ

### Telegram 隱私服務契約（2026-09-29）

`tests.test_privacy_service` 驗證：Adapter 必須提供 `read_privacy_state()`，回傳
`verified=true` 與 boolean `privacy_mode`。控制前無法讀回狀態時不送指令；控制後
狀態不符時不得回報成功。重複 hide/show 依相機讀回狀態保持冪等，併發請求回報 busy，
不建立等待佇列。錯誤只回傳類型，不帶 Adapter 原始訊息。這是服務契約測試，
不代表實機具有隱私功能；目前 Host 尚未接上真實隱私 Adapter。

PTZ 自動測試只驗證 localhost request、JSON body 的 allowlist、位置範圍、登入／CSRF 與
停用閘門，不會真實移動攝影機。實驗 binary、活動 producer、方向與停止回應必須另行取得
使用者授權後在隔離環境驗收，不能以 mock 結果宣稱已支援。
