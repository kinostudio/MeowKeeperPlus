# 開發路線圖

## 目前優先任務（2026-10-17）

以 [CURRENT_GOALS.md](CURRENT_GOALS.md) 執行最新任務；[正式產品規格](PRODUCTIZATION_SPEC.md)為近期功能／接線狀態與完成條件的統一入口。

| 階段 | 目標 | 目前定位 |
| --- | --- | --- |
| P0 | 最新PTZ真實讀回、來源／控制記憶、Telegram逐軌跡閉環、候選凍結 | 進行中；不能稱全部完成 |
| P1 | 可攜／安裝／升級／解除安裝／資料復原，runtime與程序所有權 | 已有實作，需跨環境重複驗收 |
| P2 | 家庭24H→7天、斷網／磁碟／AI失敗隔離、多觀看者與通知 | 待統一長期驗收，舊短測不替代 |
| P3 | 5位新手任務、跨尺寸／鍵盤／Tray、白話診斷 | 待完整可用性驗收 |
| P4–P5 | RC發布門檻、支援清單、安全／授權檢視、可恢復更新、核心1.0 | 尚未達成 |
| P6 | WebRTC／共享H.264備援、AI校準／受控訓練、更多來源 | 分項立項；不是既有支援承諾 |

產品完成不是實作項目全打勾；需要可靠交付、可恢復資料、可理解的狀態與持續維運。基本監控可先正式化，未驗收身分／行為維持experimental。

## 以下為歷史版本階段（不作目前完成度或部署判定）

### 2026-09-28 優先任務快照

以 [CURRENT_GOALS.md](CURRENT_GOALS.md) 路由最新證據；Telegram「是貓咪／不是貓咪」
回饋從後期 V0.5 提前至下一個最小閉環，依 [回饋規格](TELEGRAM_CAT_FEEDBACK_PLAN.md)
已完成來源碼中的按鈕、授權接收、冪等偏移與本地標註統計；r23 可攜包已建立並通過包檢核，但目前 Host 仍執行 r22。需啟動新版並完成真實 Telegram 正、負按鈕點擊驗收後，才能標記端到端完成。再依標註樣本評估門檻。舊章節為階段規劃，不代表目前未完成或已驗收。
不新增模型、不自動訓練、不重做已通過的兩小時觀測與 FFmpeg 終止恢復。

## Phase 0：相容性與風險確認

### 目標

在建立正式 GUI 前，確認實機媒體、ONVIF、Telegram、FFmpeg 與推論後端；開發／建置階段
自動取得並驗證固定官方 YOLO11n 權重，不要求最終使用者處理模型檔。
效能判定使用 `mx250_minimum`：MX250、2 GB VRAM 預算、8 GB RAM 最低、16 GB 建議。

### 交付

- `system_probe.py`：Windows、Python、CPU、RAM、GPU、驅動、推論 Provider、磁碟。
- `ffmpeg_probe.py`：FFmpeg/FFprobe 路徑、版本與基本能力。
- `camera_probe.py`：網路可達性、由環境變數提供的 RTSP URI、串流 metadata。
- `telegram_probe.py`：Token 驗證、選用測試訊息、Chat ID 權限前置確認。
- `run_phase0.py`：以單一唯讀命令彙整所有探針，不安裝套件或傳送訊息。
- 實測報告：明確區分成功、失敗、未安裝、未檢測、開發機與產品基線。

### 完成條件

- 找到使用者實測可用的 RTSP URI，不把推測路徑當結果。
- 確認影像編碼、解析度、FPS、音訊 stream 與音訊編碼。
- 確認可用串流數與同時開啟 mydlink App 時的行為。
- 確認 ONVIF Profile、PTZ、Preset 的唯讀能力。
- 確認本機可用推論後端；下載預設 YOLO11n 僅使用固定官方 release，並產生 manifest。
- MX250 以單一模型、batch 1、640 像素級輸入與約 5 FPS 完成後續實機基準測試。
- 一個 Bot 可對至少一個授權 Chat ID 傳送測試訊息。

## V0.1：桌面監看原型

- CustomTkinter 設定與即時預覽。
- RTSP、YouTube、影片檔與 Mock `MediaSource`。
- 固定版 `yt-dlp`／`yt-dlp-ejs` 解析 YouTube URL，FFmpeg 以單一程序輸出低頻 JPEG 影格；
  adapter 具備 metadata、max_height 與 3／6／12／24／60 秒重新解析退避。
- 可替換影像分析介面與本地 YOLO 貓咪偵測適配器。
- 以官方 GitHub `yolo11n.pt` 作為已驗證的預設權重，記錄來源與 SHA-256。
- 事件分析結果可依冷卻策略傳送至多個 Telegram Chat ID。
- Windows 發布 runtime 包含專案內 `vendor/ffmpeg`，不要求使用者個別安裝 FFmpeg。
- 繁體中文日誌與分層錯誤。
- 設定驗證、一般設定保存與秘密索引。
- 斷線重連與基本健康狀態。

不包含正式 AI 行為分類、YouTube 推流、家貓專用模型微調或完整 Windows 安裝程式。

## V0.2：多貓身分與行為分析基礎版

- BoT-SORT 短期多目標追蹤與來源／PTZ 變更 reset。
- 可配置 `PetRegistry` 與 `tools/enroll_pet.py`，不寫死寵物名稱。
- 官方 DINOv2 ViT-S/14 低頻 embedding、HSV 輔助、unknown 與時間投票。
- food/water/bed/litter/play/excluded normalized zones、規則 FSM、hysteresis 與用餐／飲水合併。
- SQLite 事件、人工標籤、身分修正與模型版本表；不逐影格寫入。
- 音視融合只產生 `possible_vomit` 疑似候選；YAMNet 與 R3D18 仍為可選介面。
- 仍沿用單一媒體入口與既有 Telegram；GUI、姿態模型與完整模型訓練延後。

## V0.3：穩定性與可視化整合

- 依 Phase 0 結果實測 ONNX Runtime CUDA；不穩定或不相容時降級 CPU，TensorRT 不阻擋 V0.3。
- 將 V0.2 協調器接入 CustomTkinter 預覽、狀態燈、事件時間軸與每日統計。
- 加入壓力測試、掉幀／延遲／VRAM 監控與磁碟保留策略。
- 以單一媒體入口完成服務生命週期與斷線重連；不增加第二條 RTSP。

## V0.2B：24H 行為優先推論架構（本階段）

- `GPUInferenceScheduler` 統一 HIGH 行為與 LOW 身分工作，單工作者、有界佇列、去重與逾時。
- 行為事件可以先以 `unknown` 建立與保存，DINOv2 完成後補寫同一 `event_id` 並保留稽核。
- `GPUResourceMonitor` 與 `InferenceLoadController` 支援 NORMAL／THROTTLED／CRITICAL／
  RECOVERY 及一般 3 FPS、活動 5 FPS、安靜 1 FPS 的保守策略。
- `NotificationDispatcher` 將 Telegram 送出移至背景；不得因網路延遲停止主要分析。
- `tools/gpu_probe.py` 只讀取 PyTorch CUDA、ONNX providers 與可選真實模型實測，不自動安裝。
- 未執行 30 分鐘以上壓力測試的項目在報告標示 `not_checked`，不把本機短測當成 24H 證明。

## MVP Release Gate：24H 基礎監控收尾

本 gate 只收斂既有 MVP 的穩定性與文件真實性，不新增 AI 模型、行為種類、ONVIF/PTZ 或
大型 GUI。會逐項盤點單一媒體入口、YOLO11n cat-only、冷卻、Telegram 圖片、CPU fallback、
SQLite migration/WAL、FFmpeg 有限重啟、保留／磁碟工具與 Windows 可攜式包。壓縮片段接線、
事件 MP4、正式 24H 服務及家庭資料驗收若沒有證據，必須標為 `interface_only`、
`not_implemented` 或 `not_checked`。

V0.2 身分與規則行為繼續以 `experimental` 發布；所有未知身分事件先落地，不可因背景
解析或 GPU 錯誤丟失影音、事件或通知主路徑。

## V0.4：家庭資料閉環與行為校正

- 擴充前端寵物登錄、參考影像與門檻校正。
- 以人工確認資料校正毛色／外觀 Re-ID、用餐、飲水、睡眠與玩耍規則。
- `motion_state` 活動量、跳躍候選與日報回歸測試。

## V0.5：聲音與複合事件

- YAMNet 或替代音訊後端評估。
- 一般貓叫、哭叫、疑似乾嘔候選。
- 音訊加視覺的複合判斷。
- Telegram 正誤回饋與本地標註閉環。

## V0.6：ONVIF 隱私控制

- 顯示既有 Preset。
- 明確授權的 `/hide`、`/show`、`/private`。
- PTZ 移動期間暫停行為判斷。
- iOS 捷徑透過 Telegram 間接控制。

## V0.7：選用 YouTube 推流

- 使用同一媒體入口分流。
- 可直接 copy 時避免重新編碼。
- AI 分析走降採樣支路。
- Stream Key 使用安全儲存。

## V1.0：部署版

- Windows 背景服務與登入後 GUI。
- 穩定 IPC、資料備份、遷移與升級復原。
- 磁碟容量管理、保留政策與長時間壓力測試。
- 安裝程式、自動啟動與解除安裝。
- 第三方套件、模型、資料集與商業散布授權審查。

## 延後項目

- SLEAP/DeepLabCut 精細姿態。
- 完整兩貓永久 Re-ID 模型。
- 自動模型再訓練。
- 多攝影機協同。
- 多 Bot。

延後不代表排除；這些項目需要先有穩定媒體與真實標註資料。
# Phase 3（目前）

- 建立 FastAPI `/api/v1` 與本機 server-rendered Dashboard。
- 建立 Tray localhost client、單一實例鎖與 Task Scheduler 啟動說明。
- 建立 Tailscale 官方 CLI 唯讀狀態／Serve 計畫與 Xiaomi/go2rtc 探測契約。
- 先完成自動 smoke／安全測試，再進行 Web runtime、Tailscale、YouTube、D-Link RTSP、
  真實 Telegram、Xiaomi 與 MX250 的家庭實機驗收。

Phase 3 不包含新 AI 模型、行為種類、ONVIF、PTZ、Thingino 刷機或大型 GUI。

## Phase 3B：Windows 啟動與外部來源邊界（目前狀態）

- 啟動器、runtime bootstrap、localhost Host 啟動、Edge Dashboard 與真實 pystray smoke 已
  在本機驗證；完整 Windows 通知區視覺驗收仍需使用者桌面確認。
- 官方 go2rtc `v1.9.14` Windows x64 binary、SHA-256 manifest、localhost process lifecycle
  與 `/api/v1` integration routes 已驗證。
- Xiaomi 設定精靈可安全準備 runtime、開啟 localhost 登入頁、保存遮罩狀態並等待登入；真實
  Xiaomi 帳號登入、驗證碼、設備發現、profile 品質與來源切換仍為 `waiting_for_user`／
  `not_checked`，不得當作已完成功能。
- recent clip 時間軸已改以來源 session 的媒體時間重建，避免本地檔案快速讀取時以檔案 mtime
  造成 `/live` 片段長度錯誤。

完整證據與限制見 `docs/MVP_PHASE3B_LAUNCHER_UI_REPORT.md`、`docs/XIAOMI_GO2RTC_INTEGRATION.md`
與 `docs/MVP_PHASE3B_WORKLOG.md`。
