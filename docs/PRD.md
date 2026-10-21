# 產品需求文件（PRD）

**2026-10-21 測溫啟用體驗**：首頁讀不到 CPU 溫度時提供「啟用／檢查測溫」連結；趨勢頁提供情境說明與明確操作按鈕。只允許已登入、通過 CSRF 且本機連線的互動 Host 開啟內附並通過 manifest 校驗的啟用工具；遠端只讀指引。首次使用不自動安裝核心驅動，不改 Host 帳號，官方授權條款／UAC 由使用者親自確認；工具結束不等同真實溫度驗收。

**2026-10-19 追加授權**：測溫採固定 sensor-only SYSTEM工作與一般帳號Host分離；只接受有界時間戳、經受保護本機快取回傳，不開網路埠、不接收任意命令。明確 `-UserHost` 保留憑證邊界，但登入Windows後才啟動。見 [安全邊界與待驗項目](CPU_SENSOR_BROKER_2026-10-19.md)。尚未部署或驗證目標筆電連續溫度。

**2026-10-19 測溫安裝**：Windows 安裝流程提供明確同意的 CPU 測溫選項；缺少 PawnIO 時由官方固定版本下載並驗證 SHA-256／Authenticode，系統安裝沿用既有管理員授權。保留官方授權條款互動，不放寬驅動權限或自動重開機。驅動失敗不阻擋影音；以持續真實溫度而非安裝結束判定測溫成功。可攜版有獨立授權入口，一般帳號權限不足仍如實提示。

**2026-10-18 相容預覽**：原碼 WebRTC／MSE 不相容後，才啟動單一共用 H.264 MSE，最後觀看者離開即清理。硬體初始化不可用採有明確提示的720p／15 FPS CPU上限；硬體路徑最高1080p／20 FPS，不改AI／錄影。[隔離驗收與限制](ON_DEMAND_H264_PREVIEW_2026-10-18.md)。

**2026-10-20 播放與事件回顧**：H.264 可先走低延遲 WebRTC；HEVC 不先試 WebRTC，瀏覽器宣告支援時優先使用原碼 MSE，不支援或播放失敗才走單一共用 H.264 相容分支，最後才回退節能 JPEG。事件列表播放在登入保護的彈出視窗先讀原片；瀏覽器不相容時使用本機有期限／容量上限的轉碼快取，不覆寫原片。人工回報片段以單一雙把手時間軸選起點與終點；首頁事件熱點依最近七個完整本地日 × 每日24小時分格。隔離程式測試不等同 C701 實機瀏覽器解碼驗收。

**2026-10-18 攝影機接入／監控改版**：三步一鍵接入、逐裝置本機／中繼選擇、同程式受限中繼（首次不啟動 AI／Telegram）、影片內保存提示、分組規格、讀回確認的點位定位與硬體首頁，見 [接線與隔離驗收](CAMERA_MONITOR_REDESIGN_2026-10-18.md)。未建置或部署。

**2026-10-19 安裝資料與事件影片**：正式安裝的 Host、Tray、資料庫、寵物資料、事件與設定必須使用單一 `ProgramData\MeowKeeperPlus\Data` 根目錄；所有直接啟動預設共用同一 resolver。升級需在替換舊 App 前安全遷移歷史 `App\data`／根目錄設定檔；遇到同名資料即停止，不覆寫。跨機複製後，事件播放可以在當前事件資料根目錄按安全 event ID 找回原檔名影片，不得按資料庫中任意舊絕對路徑取檔。防火牆檢查頁必須顯示 Xiaomi UDP 診斷結果；Dashboard 自身的 LAN TCP 規則是另一項狀態，不代表攝影機取流正常。實作／回歸與未驗證範圍見 [CURRENT_GOALS.md](CURRENT_GOALS.md) 與 `configs/release-regressions.json`。

**2026-10-17 整合**：近期需求、已接線／待實作／待驗收矩陣，以及1.0產品化門檻以
[PRODUCTIZATION_SPEC.md](PRODUCTIZATION_SPEC.md) 為準；當前任務見 [CURRENT_GOALS.md](CURRENT_GOALS.md)。
本文保留原始產品範圍，不能把規劃項目當成已支援。近期Web Dashboard／MSE／PTZ／V5規格優先於舊原型敘述。

Telegram回饋已不只「是貓咪／不是貓咪」：依寵物檔案動態提供個體／其他貓／不是貓，再對指定軌跡回報行為與可選學習同意；歷史規劃見
[TELEGRAM_CAT_FEEDBACK_PLAN.md](TELEGRAM_CAT_FEEDBACK_PLAN.md)，近期接線見[AI身分驗收](AI_IDENTITY_SPEC_ACCEPTANCE_2026-10-14.md)。人工標籤不改寫原始模型分數；未完成獨立資料驗證不得宣稱分數等於準確率。

## 1. 產品定義

2026-10-17 CPU 觀測增補：預設 5 秒背景共用取樣，已登入 Dashboard 讀快取；超過 15 秒不顯示舊數字為即時。CPU Package／Tdie 與主機板溫度分開，缺失保存 NULL，歷史每分鐘記錄。不得自動提權或安裝驅動；此來源不自動改動既有 AI 溫控。Repository 接線與實機／交付限制見 [持續觀測報告](CPU_TEMPERATURE_CONTINUOUS_INTEGRATION_2026-10-17.md)。

- 中文名稱：喵喵守護者+
- 英文名稱：MeowKeeper+
- 英文副標：MeowKeeper+ — Local AI Pet Guardian
- 中文副標：本地 AI 全天候守護每一隻貓咪

MeowKeeper+ 是 Windows 10/11 的本地寵物 AI 監控套件。系統以單一 RTSP 攝影機輸入為核心，在本機分流至預覽、事件快取、視覺／音訊分析與選用推流；異常事件透過一個 Telegram Bot 通知多個獲授權使用者。

## 2. 產品原則

1. 本地優先：影像、音訊、模型推論與行為資料預設留在本機。
2. 漸進可驗證：先完成 Phase 0 實機檢測，再依 V0.1、V0.2 演進。
3. 單一攝影機入口：避免 GUI、AI、錄影與推流各自建立 RTSP 連線。
4. 可替換來源：所有核心流程可使用 RTSP、影片檔或 Mock 來源。
5. 安全預設：秘密不進 Git；ONVIF 預設唯讀；攝影機寫入動作需明確授權。
6. 不誇大 AI：疑似事件要保留分數、判斷依據與人工確認。

### 2.1 最低硬體基線

- Profile 名稱：`mx250_minimum`。
- 參考 GPU：NVIDIA GeForce MX250，以 2 GB VRAM 為最低預算。
- 系統記憶體：8 GB 最低、16 GB 建議；較多 RAM 不自動提高 GPU 推論負載。
- AI 預設：單一視覺模型、batch 1、640 像素級輸入、約 5 FPS 分析採樣。
- 優先實測 ONNX Runtime CUDA；無法使用時降級 CPU。
- TensorRT、姿態模型、同時多模型與高 FPS 不列為第一版必要條件。
- 任何較高規格開發機的結果都不能代替 MX250 實機效能與長時間穩定性測試。

## 3. 使用者與情境

### 主要管理者

- 設定攝影機、Telegram 使用者、資料保留與模型參數。
- 可查看影像、控制允許的 PTZ Preset、管理其他使用者權限。

### 家庭成員

- 依個別權限接收一般通知或異常警報。
- 可選擇只允許 `/status`、`/snapshot`、`/live`，不允許 PTZ 或隱私控制。

### 核心情境

- 全天候監控寵物活動並產生個別統計。
- 偵測疑似乾嘔、異常叫聲或長時間不活動並附事件片段。
- 由 Telegram 取得快照、最近數秒影片與系統狀態。
- 回家時將鏡頭移至既有隱私 Preset；離家時回到監控 Preset。

## 4. 功能需求

### 4.1 媒體輸入

- 支援 RTSP 主來源與 YouTube 影片／直播頁面作為分析來源。
- YouTube URL 由固定版 `yt-dlp`（搭配 `yt-dlp-ejs` 與已檢查的 JavaScript runtime）
  解析為短期實際串流 URL；解析失敗時顯示繁體中文提示，不猜測或把簽名 URL 寫入設定、
  資料庫、日誌或報告。私人、會員、年齡限制、DRM 與 Cookie 登入流程標為
  `unsupported`，不嘗試繞過。
- YouTube source type 為 `youtube`；設定只保存原始 `youtube.com`／`youtu.be` 頁面 URL、
  `max_height`（360／480／720／1080）與 video/audio 開關。預設 video=true、audio=false。
- 解析結果至少保存於記憶體狀態：video_id、title、is_live、live_status、selected_height、
  video_codec、audio_codec；短期媒體 URL 不可進入設定或長期資料。
- FFmpeg與本地共享影格供分析使用，不為預覽與AI各建來源連線。JPEG寬度政策、YOLO輸入長邊與身分裁切需求分開；現行JPEG最高寬度1920，基本AI基線仍為640像素級、約5 FPS，允許進階尺寸並依資源降階，非所有硬體保證。
- 首頁預設節能JPEG，即時頁預設MSE原碼連續影片，休眠停止該頁媒體但不停止核心。原碼尺寸不受JPEG上限約束；WebRTC與按需共享H.264備援尚未實作，不宣稱已有。
- 一個攝影機原則上只有一條必要 RTSP 輸入。
- 本地媒體層分流給 GUI、AI、音訊、環形快取與選用 YouTube 推流。
- 環形快取保存壓縮影音片段，不以長時間原始 BGR 影格為主要格式。
- `/live` 預錄、後錄與總快取長度均可設定，且預錄不得超過快取長度。
- H.264/H.265 關鍵幀不足時可回傳稍長片段；精確裁切需明確選擇重新編碼。
- Telegram 影片送出前必須檢查 codec、像素格式、尺寸、時長及 MP4 索引。H.264 +
  `yuv420p` 且 `moov` 已前置的檔案直接送出；HEVC/H.265、非 `yuv420p`、索引不合格
  或超過大小上限時，才建立 H.264/AAC、`yuv420p`、`+faststart` 暫時副本。原始檔案
  不被覆寫，轉碼失敗改送事件 JPEG。

### 4.2 GUI

- 現行主要介面為FastAPI／Jinja Web Dashboard與Windows Tray；CustomTkinter屬歷史原型。核心不得依賴瀏覽器或特定GUI框架。
- GUI 與背景服務分離；關閉 GUI 不應停止 24 小時監控。
- 提供設定精靈、即時預覽、繁體中文狀態燈、白話文日誌與測試按鈕。
- GUI 預覽更新率與 AI 分析採樣率分離，避免預覽畫面迫使 MX250 提高模型推論頻率。
- 敏感欄位只顯示遮罩與安全儲存狀態。
- AI／行為參數頁透過受保護 API 保存非秘密設定；信心門檻、採樣 FPS、冷卻與事件前後錄
  均受 `mx250_minimum` 範圍限制，保存後由安全重啟套用，不在執行中的 ingest 上建立第二條
  來源或 FFmpeg。
- 未知 RTSP 路徑不得提供偽裝成確定答案的預設值。

### 4.3 Telegram

- 第一版使用一個 Bot、多個 Chat ID。
- Dashboard 可為每個 Chat ID 保存自訂別名與接收開關；只有勾選的通知對象會在 Host
  啟動時自動載入。憑證與通知對象清單均保存於 Windows Credential Manager，頁面只顯示
  Chat ID 遮罩與對象數量。
- 保存設定不會偷偷重建執行中的 Host；使用者可按「啟用 Telegram 並重新啟動 Host」
  一次套用，之後每次 Host 開啟會自動接通已保存且啟用的通知對象。
- 每個使用者可分別具有：
  `notifications`、`alerts`、`view_camera`、`control_camera`、`admin`。
- 程式可自動同步 Bot 指令選單及訊息內 Inline Keyboard。
- `/snapshot` 回傳目前畫面。
- `/live` 依 GUI 設定回傳前錄／後錄片段。
- `/status` 回傳服務、串流、AI、CPU/GPU、儲存空間與最近錯誤摘要。
- 已授權的私人 Chat ID 可用 `/catnotify on|off|status` 控制自己的貓咪偵測事件通知，
  `/mute N` 靜音 1～168 小時，`/unmute` 提前解除；其他通知種類與其他收件者不受影響。
  偏好在 Host 重啟後保留，靜音到期自動恢復，但總開關關閉時不會自行打開。
  此功能只限制 Telegram 送出，不停止本地貓咪偵測、SQLite 事件或影片保存；
  首次使用仍須由 Dashboard 安全啟用 Bot 與通知對象。
- `/hide`、`/show`、`/private` 等控制指令必須通過權限檢查。
- 冷卻去重只限制通知轟炸，不應吞掉資料庫中的原始事件。

### 4.4 AI 與個體辨識

- 視覺初版以可替換的 YOLO 相容偵測介面尋找貓咪與相關物件。
- V0.1 提供本地 Ultralytics YOLO 影格分析適配器，預設權重為專案內已驗證的
  `models/yolo11n.pt`；開發／建置階段由 `fetch_model.py` 從固定官方 GitHub
  `ultralytics/assets` release 自動取得，建立 `models/model_manifest.json` 並在啟動時
  驗證來源、大小與 SHA-256；最終使用者不需自行搜尋、下載或輸入路徑。
- V0.1 只保留 COCO `cat` class id `15`，忽略其他類別；信心門檻預設 `0.45` 且由設定
  管理，不散落為程式常數。
- 真實驗收需使用至少一張含貓圖片、一張非貓圖片及本地影片；Telegram 未明確啟用時不得
  傳送任何外部訊息。
- 偵測到事件後可將當下 JPEG 影格傳送至獲授權 Telegram Chat ID；同類事件依冷卻
  設定去重。
- detector 產生的 `cat_detected` JPEG／MP4 事件需提供「是貓咪／不是貓咪」回饋；
  僅允許已選取私人通知對象回覆其實際收到的事件。人工標籤以 SQLite 保存，不改寫模型原始分數。
- AI 設定頁顯示回饋數量、不同意見、模型分數分桶及已審核 alert precision；此比例只涵蓋已告警且一致標註的事件，不能稱為整體準確率、recall 或校準機率。
- ByteTrack 只負責短期軌跡；不得將追蹤 ID 當成永久寵物身分。
- 寵物名稱、外觀、項圈、參考影像與門檻由前端管理。
- 個體辨識可逐步融合毛色、外觀 embedding、Re-ID、項圈與人工確認。
- 模型後端以抽象介面隔離，Phase 0 後才依實機選定 CUDA、OpenVINO 或 CPU。
- `mx250_minimum` 優先測試 ONNX Runtime CUDA，若不相容或不穩定則使用 CPU 降級。
- 第一版同時只允許一個視覺模型、batch 1 與不高於 5 FPS 的預設 AI 採樣。
- 正式散布前需檢查 YOLO、模型、資料集與所有第三方依賴授權。

#### V0.2 多貓個體辨識基礎版

- BoT-SORT 只維持短期 `track_id`；`persist=true` 與 `with_reid=true` 不代表永久身分。
- 寵物由使用者登錄，名稱與數量不限；未知貓咪保留為 `unknown`，不強行配對。
- 預設官方 DINOv2 ViT-S/14 只在新 track、低信心、遮擋或低頻率條件取 embedding，並融合
  多個 L2 embedding 與 HSV 毛色輔助；模型檔與 manifest 由開發／建置工具取得並校驗。
- MX250 下身分採樣最多約 1 FPS；每隻追蹤同時只使用有限參考向量，避免把 DINOv2 變成第二個
  全幀高頻模型。
- PTZ、來源或場景變更時清除短期 track、身分投票與行為狀態；追蹤 ID 不跨場景延續。

### 4.5 行為與分析

- 使用 [行為資料模型](BEHAVIOR_MODEL.md) 定義的三個維度。
- 主要行為互斥；動作狀態與附加事件可重疊。
- `running` 不直接等於 `playing`。
- 每隻寵物分別計算睡眠、休息、進食、喝水、玩耍、理毛、貓砂與活動量。
- 進食與飲水具最短時間、事件中斷合併時間與信心門檻。
- 身分 `accepted_threshold`、`unknown_threshold`、`minimum_margin`、時間投票視窗、DINO
  採樣頻率與行為採樣頻率均屬版本化設定，可由未來 GUI 覆寫。
- 報告提供今日、昨日、7 日、30 日與自訂期間。
- 同時顯示占日曆時間與占有效監控時間的比例。
- 不在畫面、攝影機斷線或無法辨識不得歸入睡眠。

- 場景物件參考與寵物身分照片分開管理；使用者可保存目前影格或上傳參考照片，直接拖曳圈選飯碗、水盆、貓砂盆等物件並產生本機拼貼，不要求輸入百分比座標。參考圖預設不外送。
- 若啟用選用圖片 API，只有在事件圖片外送已同意、另外開啟場景參考並對目前服務／模型再次同意後，才附送最多 8 個已啟用物件的裁切與名稱；隱私／排除區不會加入。場景拼貼只作辨認物件的上下文，不作為事件當下、貓咪身分或行為的證據。

V0.2 先使用 `RuleBehaviorClassifier`、hysteresis 與事件合併；R3D18 只提供需自備核准
checkpoint 的時序介面。沒有家庭實拍標註前，不啟用姿態模型、同時多模型或高 FPS 行為模型。
`possible_vomit` 只能由音訊與視覺證據融合產生疑似候選，不能當成醫療診斷。

### 4.6 事件錄影

- 一般事件預設可配置為前 5 秒加後 10 秒。
- `/live` 預設為前 5 秒加後 0 秒。
- 高疑慮異常可使用不同前後錄長度。
- Telegram 上傳前檢查平台限制；超限時依序嘗試壓縮、縮短或改送截圖與文字警報。
- 事件片段需保存來源時間戳、觸發依據、模型版本與人工確認。

### 4.7 隱私與 PTZ

- Phase 0 只探索 ONVIF 能力並讀取既有 Preset。
- PTZ Preset 的呼叫與建立是明確的使用者動作。
- 攝影機移動期間暫停行為判斷，穩定後重建追蹤狀態。
- 不把攝影機控制 API 暴露到公網；iOS 捷徑透過獲授權 Telegram 指令間接控制。

### 4.8 資料與保留

- `config.json` 保存版本化的一般設定。
- Windows Credential Manager 或 DPAPI 保存正式秘密；開發期可使用未追蹤 `.env`。
- SQLite 保存事件、行為區間、分數、模型版本、使用者確認與影片索引。
- 預設保留政策可設定；低磁碟空間時先停止非必要錄影並告警，不刪除人工鎖定事件。
- 進階設定提供完整資料 ZIP 匯出／匯入，至少使用 6 字元備份密碼；包含設定、SQLite、Telegram
  偏好、寵物 profile／參考影像／embeddings 與已完成事件檔案，不包含暫存 cache、模型或 runtime。

## 5. 非功能需求

- Windows 10/11 64 位元、Python 3.10 以上。
- 最低運行基線為 MX250、2 GB VRAM 預算與 8 GB RAM；16 GB RAM 為建議配置。
- 串流中斷具分層錯誤與有上限指數退避。
- 24 小時服務不依賴 GUI 存活。
- Phase 0 不自動安裝 Python、FFmpeg、CUDA、驅動或大型額外模型；預設 YOLO11n 是由
  開發／建置工具固定來源取得並驗證，且不進行有風險的 ONVIF 寫入。
- 所有外部程序具逾時且不使用 `shell=True`。
- 使用者可見介面、提示與日誌為繁體中文。

## 6. 本次範圍

本次已交付可替換媒體來源、YouTube 解析、FFmpeg 影格管線、已驗證 YOLO11n 預設模型、
真實貓咪影像推論與 Telegram 事件圖片推播骨架，並新增 V0.2 的多貓短期追蹤、可配置寵物
登錄、DINOv2 身分原型、區域規則、SQLite 事件契約及音視疑似事件介面；完整 GUI、姿態模型、
家庭專用時序模型、Windows 服務與安裝程式仍不在範圍。

## 7. 驗收準則

- 文件與程式共用相同行為列舉及安全原則。
- 四個探針均可顯示 `--help`。
- 缺少 FFmpeg、攝影機、GPU 或選用套件時不會未處理崩潰。
- 探針不輸出含密碼的 RTSP URL 或完整 Token。
- 最小單元測試無需實機即可通過。

## 8. V0.2B 24H 行為優先與 MX250 負載契約

系統執行順序固定為：影像／環形快取不中斷 → 偵測貓咪 → 判斷動作與行為 → 建立事件
並保存影片索引 → 發送必要通知 → 背景確認個體身分 → 更新統計。身分結果延遲、未知、
逾時或 CUDA 失敗都不得阻止 Priority 0～2。

`mx250_minimum` 預設為一般 YOLO 3 FPS、活動增強 5 FPS、安靜 1 FPS、行為 1 Hz、
batch 1、640 像素級輸入、單一高頻視覺模型；DINOv2 為 LOW、按需且最多兩筆 pending
工作。8 GiB RAM 是最低值、16 GiB 是建議值，16 GiB 不會自動提高 GPU 負載。

匿名事件必須先可見，例如「神秘喵喵進入飯碗區／好像在吃喵糧哦」；背景身分完成後
補寫同一事件，並保留 unknown 到已知身分的修正紀錄。Telegram 與 SQLite 皆不得阻塞
影像／行為主路徑；通知使用有界背景佇列，資料庫在事件開始、完成或身分更新時寫入。

## 9. MVP Release Gate 真實性界線

MVP Phase 2 已提供單一 FFmpeg ingest、YOLO11n `cat`（COCO class 15）、CPU fallback、
冷卻、壓縮分段環形快取、事件 MP4 組裝、`/live` 片段服務與 Telegram 影片佇列的本地
可測試主流程。YouTube、D-Link RTSP、真實 Telegram Chat ID、MX250 長時間穩定性及
正式 Windows 服務仍需實機驗證；未完成前不得宣稱可對外發布。

身分辨識、寵物登錄、外觀特徵與規則式行為分析屬 V0.2 **experimental**。沒有家庭攝影機
實拍標註與準確率驗收時，結果只能稱為候選事件；身分不確定先保存 `unknown`，稍後才可
補寫同一事件。

開發環境不得未經確認修改 Python、驅動或 CUDA。Windows 發布包必須包含 Python runtime、
FFmpeg、必要套件與已驗證模型；終端使用者不得建立 venv、執行 pip 或手動下載模型，建置
流程見 [BUILD_WINDOWS.md](BUILD_WINDOWS.md)。
# Phase 3 操作介面與遠端邊界

正式桌面操作介面改為本機 Web Dashboard（`http://127.0.0.1:8000`），Windows Tray 僅作
輕量入口。Dashboard 關閉、手機瀏覽器離線或 Tray 退出時，24H 核心仍持續執行。API 使用
版本化 `/api/v1`、應用層驗證、CSRF header、有界 SSE；不預設監聽 `0.0.0.0`、不提供公開
Webhook、不把事件資料夾掛成靜態目錄。

頁面最少包含總覽、即時畫面、事件影片、來源設定、AI／行為參數、Telegram、Tailscale、
診斷、日誌及進階設定。即時畫面必須沿用單一 `FfmpegMediaIngest` 的 JPEG 或本地 restream，
不得另開第二條 RTSP。Tailscale Serve 是可選私人遠端方式；Funnel、Subnet Router、
Thingino 與 Xiaomi 免刷機 bridge 需明確確認及實機驗證，不能視為 MVP 已支援。

遠端存取說明採本機優先：MeowKeeper+ 預設不把監控服務公開到網際網路。使用者可在主機與
手機／外出電腦安裝並登入官方 Tailscale，再以加密私人連線登入 Dashboard；Tailscale 會
嘗試直接點對點連線，必要時可使用其加密中繼。Tailscale online／active 不代表有人正在看，
Dashboard session 與實際影格／媒體觀看需分開顯示；不以裝置在線狀態推斷觀看者。

## Phase 3E 區網 Dashboard

`dashboard.lan_access_enabled` 預設為 `false`，Dashboard bind 預設
`127.0.0.1`；只有使用者在受保護設定頁明確開啟並重新啟動後，才 bind
`0.0.0.0`。Port 沿用現有 Dashboard 設定，不在前端或 launcher 另開固定 Port。

區網頁面仍使用既有登入、Session、CSRF 與失敗限制。所有狀態、預覽 JPEG、事件 MP4、
下載與診斷都走同源受保護 API；go2rtc管理API與SQLite保持本機，RTSP預設本機。使用者另行授權的Private LAN RTSP屬獨立選項，會繞過Dashboard登入，不默認開放WAN或1984管理API；不能把它描述為受Session保護。
預覽沿用單一 ingest 的最新 JPEG，每個可見頁面最多 2 FPS，頁面隱藏或離開時停止請求。

Windows 防火牆是可選操作，只能建立或移除 `MeowKeeper+ Dashboard LAN` 的 TCP Dashboard
Port／Private Profile 規則；無管理員權限時安全回報，不得讓 Host 啟動失敗。可用區網網址
由啟用中的非虛擬私人 IPv4 動態計算，不永久寫死於設定。

Host 可在 `/healthz` 確認成功後，經由既有有界 Telegram outbound queue
選擇性傳送啟動摘要。摘要只包含實際 LAN Dashboard URL、本機 URL、官方
Tailscale 唯讀狀態、來源類型與 AI 設定狀態；`0.0.0.0` 不得作為分享網址。
`telegram_startup_notice` 預設關閉，只有使用者明確啟用並重新啟動 Host 才送出。

## Dashboard 最小影音／通知閉環

來源與攝影機在 `/settings/source` 合併管理。使用者可選 YouTube 公開頁面、RTSP、
本機影片或 Mock；YouTube 只保存原始頁面 URL，RTSP 帳密不可由 Web 設定保存。保存來源
後，按開始監控會接入既有單一 `FfmpegMediaIngest`：影格先交給預設 YOLO11n 的 COCO
`cat` class 15，符合信心門檻且通過冷卻後建立事件，事件影片保存 SQLite／MP4，再由
Telegram 背景佇列以 Bot API `sendVideo` 或 `sendPhoto` 通知。Telegram Token／Chat ID
可由已登入Dashboard安全保存，優先使用Windows Credential Manager；環境變數為替代來源，不回填秘密到頁面或一般設定。實際通知啟用與憑證可用狀態分開顯示，不以「已保存」冒充Bot已接通。

Telegram指令／回饋接入既有polling路徑，不要求普通使用者架公開webhook。若未來另提供webhook部署，須獨立驗證HTTPS、來源驗證與單一更新接收者，不能和polling重複處理。同版本真實訊息／按鈕／SQLite閉環仍需實機驗收。
## 可選 AI 動作分析的設定保存

服務／模型分別保存額度與圖片品質。切換目的地時，以目標模型的已保存設定或預設值為基礎，再套用當次明確提交的修改；不可因切換而靜默丟棄使用者本次變更。頁面提交時，未修改的舊模型欄位不應覆寫目標模型的設定。

停用時可保存未填完整的服務草稿；已填網址仍必須符合秘密與傳輸安全規則，已填模型仍須符合識別格式。草稿不得啟用、建立分析服務或發送連線檢查；需完整網址、模型與新的目的地圖片同意後才能啟用。模型／服務切換時，一般圖片與場景參考兩種同意均重新確認。

## Xiaomi MISS PTZ（實驗功能）

MVP 不依賴 PTZ。若維護者另行建立並驗證固定 commit 的 Xiaomi MISS go2rtc 實驗版，產品可
在使用者明確啟用後提供左右上下與停止控制。正式官方 go2rtc v1.9.14 仍為預設，沒有活動
Xiaomi producer 或未通過 Dashboard 登入／CSRF 時不得送出任何 PTZ 指令；實驗功能不可被描述
為已完成的家庭環境驗收。完整來源、build 與安全邊界見 `docs/XIAOMI_MISS_PTZ_EXPERIMENT.md`。
