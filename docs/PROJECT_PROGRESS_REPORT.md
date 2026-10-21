# 喵喵守護者+ 專案執行進度報告

日期：2026-08-04

> 以下 2026-08-04 數值為當時快照；最新差異請先閱讀下節，不要把舊的
> Xiaomi blocked、測試數量或 Ruff 失敗當作 2026-09-20 現況。

## 2026-09-21 可攜式包驗證更新

- 已在 repository-local embedded Python 3.13.15 安裝並匯入必要套件，產生
  `dist/MeowKeeperPlus-portable`（18,656 個檔案，含 Python、FFmpeg、Node、go2rtc
  及 SHA-256 驗證的 `yolo11n.pt`）。未修改系統 Python、驅動或 CUDA。
- 修正建置時誤排除 PyTorch `torch.utils.data` 與 Ultralytics `ultralytics.data`
  的問題；只允許這兩個套件模組，私人 `data`／`reports`／`tests` 仍不進入包。
- 使用**封裝內** Python／模型對本地真實圖片推論：含貓圖片 2 個 cat box，
  非貓圖片 0 個。實際後端為 PyTorch CPU，未驗證 GPU。
- 封裝內 Dashboard 曾以本機 `127.0.0.1:18080` 啟動，`/healthz` HTTP 200、
  未授權狀態 API HTTP 401；測試完已停止。最新全部 194 項 unittest、Ruff 與
  compileall 通過。這仍不等於 12–24 小時實機驗收。
- 開機排程 XML 已改為啟動正式 `run_host.py --no-open-browser`，不再啟動缺少 Dashboard
  且依賴來源環境變數的舊 `run_monitor.py`；開機核心與登入後 Tray 的 XML 均通過
  Windows Task Scheduler COM 記憶體內解析。**尚未註冊工作或重開機驗收**；SYSTEM
  與一般使用者的 Credential Manager／DPAPI 不共用，Telegram 憑證仍需在正式
  SYSTEM Host 的受保護 Dashboard 重新安全設定。
- 可攜版 YouTube 短測揭露 embedded runtime 原本缺少 `yt-dlp.exe` CLI，雖然 Python
  套件已存在仍無法啟動來源。已在 repository-local runtime 補上固定版 2026.07.04
  CLI，並讓來源 adapter 找到 `Scripts/yt-dlp.exe`；runtime capability check 現在回報
  yt-dlp、FFmpeg、FFprobe 與 JavaScript runtime 均可用。因 Google video 對 FFmpeg
  直接請求回 403，已改為固定 yt-dlp finite chunks → 匿名 pipe → 唯一 FFmpeg。
  真實公開來源第一影格、CPU YOLO 與 10-frame 壓縮 ring smoke 均通過；內容沒有貓，
  因此沒有事件或 Telegram 通知，不能推論完整事件閉環已完成。
- 新增可攜包內安裝／升級／解除安裝流程：安裝前驗證全部 manifest，應用與
  資料分別放置於 ProgramData `App`／`Data`，解除安裝預設保留資料。Windows
  PowerShell 5.1 `-WhatIf` 已通過，Core／Tray XML 已由 Task Scheduler COM 解析；
  `system_changed=false`，本機尚未註冊任務或進行重開機驗收。

## 2026-09-20 現況更新

- 可攜式包建置已排除只能使用 `.venv` 的開發期 `Start-*.cmd`／PowerShell 啟動器；此項當時只完成模擬打包契約測試。後續真實封裝與推論驗證見上節。
- 真實小米 `.55` 再走隔離、受保護的 Dashboard API：暫時 bearer 只在測試程序內使用，`/api/v1/monitor/start` 回 200，`/api/v1/preview/jpeg` 回 200 且 JPEG 有效（65,723 bytes），`/api/v1/live/recent` 回 200 且具 MP4 `ftyp` 標頭（55,547 bytes；本次未另以 FFprobe 驗證該回應）；go2rtc producer=1、consumer=1、來源連線指標=1。未授權預覽 401、停止後 404，暫時 stream 刪除後為 0。無私人影格／影片入庫、無 Telegram 對外傳送。遮罩證據見 [Xiaomi Dashboard API smoke](../reports/xiaomi-dashboard-api-smoke-2026-09-20.json)。此測試使用 noop analyzer，**不代表**正式瀏覽器 Session、YOLO 貓咪事件或 Telegram 通知已驗收。
- 受保護的 Dashboard `/api/v1/monitor/start` 已用隔離本地合成影片實際啟動單一 FFmpeg ingest；`/api/v1/preview/jpeg` 取得有效 JPEG，來源連線指標為 1，未登入時 401、停止後 404。這驗證 Web API 到媒體預覽的接線，不等於家庭攝影機在已登入瀏覽器中的畫面已驗收。
- 即時頁對 401／404／網路錯誤會撤銷並清除舊圖，避免顯示過期畫面；每個頁面同時最多一筆預覽請求，初次載入不再額外重複請求 JPEG。
- `chuangmi.camera.026c02` 已由自動後端 region 探索選到實際可開流的 `sg`，官方隔離 go2rtc 可取得真實 JPEG，單一 FFmpeg ingest 可取得影格與壓縮 MPEG-TS 分段；後端 `/live` 已組成 5.998 秒可讀 MP4。這不是「台灣帳號固定選 sg」，另一台攝影機仍需獨立驗證。詳見 [Xiaomi Region SOP](XIAOMI_MIOT_REGION_SOP.md)。
- 事件 MP4 與 SQLite `clip_status=completed` 已以**模擬事件**驗證；真實含貓影格觸發、已登入 Dashboard 預覽與 Telegram 真實傳送仍待完整端到端驗收。
- 監控停止、重啟或更換來源時不再向 Dashboard 提供上一個 session 的 JPEG。網路 ingest 遇到乾淨 EOF 仍會退避重連；一般 YouTube 影片完成則正常結束。手動停止會取消讀取／退避，不應再建立 FFmpeg producer。
- 2026-09-20 回歸：UTF-8 模式下全部 **179 項 unittest 通過**，全 repository Ruff 與 compileall 通過。測試已隔離使用者現有 Telegram 設定，沒有修改憑證或啟用外部傳送；先前 Ruff 9 項問題已修正。舊 Xiaomi trace 工具遇 `p2pid` 字串只標記 `P2PID_ERROR_TEXT_ONLY_UNVERIFIED`，不得當成 vendor／p2p_id 實際證據。
- 尚未取得 MX250、真實 Telegram Chat ID、另一台小米攝影機與 12–24 小時實機驗收；發布閘門仍為 `MVP_REAL_E2E_PENDING`。

本報告以目前 repository 程式碼、測試與本機執行證據為準；家庭攝影機、MX250、
真實 Telegram 帳號與長時間驗收尚未完成的項目，均保留為 `needs_real_data` 或
`not_checked`。

目前發布閘門：`MVP_CODE_COMPLETE`／`MVP_REAL_E2E_PENDING`

詳細驗收順序與安全證據欄位見 [MVP 真實端到端驗收清單](MVP_REAL_E2E_CHECKLIST.md)。

## 目前階段總覽

| 階段 | 狀態 | 證據／限制 |
| --- | --- | --- |
| Phase 0 相容性探針 | 已實作 | system／camera／FFmpeg／Telegram 探針與彙整入口存在；實機 RTSP、Telegram、MX250 仍待驗證 |
| V0.1 基礎偵測 | 已實作、CPU 已驗證 | 官方 `models/yolo11n.pt`、COCO cat-only、本機含貓影片成功找到貓；GPU 未宣稱已使用 |
| V0.2 身分與規則行為 | experimental | Pet registry、追蹤、特徵與規則架構存在；家庭資料準確率尚未驗收，不能當成完成品 |
| V0.2B 24H 推論控制 | 架構已實作 | 有界佇列、優先順序、CPU fallback 契約存在；MX250 及 12～24 小時壓力仍 `not_checked` |
| Phase 2 事件影片 | 已接線 | 壓縮 ring、事件 MP4、SQLite、Telegram 背景 queue 有測試；真實 Chat ID 傳送仍待驗證 |
| Phase 3A Dashboard | 已實作 | FastAPI Dashboard、共用頁面版型、保護 API 與 Session 已測試 |
| Phase 3B Windows 啟動／Tray | 已實作 | .venv launcher、Host／Tray／go2rtc 單一實例與啟動流程已測試；桌面通知區視覺仍需人工確認 |
| Phase 3C Xiaomi | experimental blocked | 登入／裝置診斷與隔離 go2rtc 路線保留；P2P 影像成功標準尚未達成，不影響一般 RTSP／YouTube 路線 |
| Phase 3D YouTube source | MVP adapter 已實作 | 固定 yt-dlp／yt-dlp-ejs、FFmpeg、metadata 與公開測試頁第一個影格已驗證；家庭直播長時間重連待驗證 |
| Phase 3E LAN Dashboard | 已實作、實機部分待驗證 | bind host、同源 URL、Session 保護、Private firewall 契約已測試；iPhone 與實際防火牆仍待人工驗收 |
| 本輪 Dashboard MVP chain | 本機閉環已驗證 | YouTube／RTSP 設定入口→單一 ingest→YOLO→事件 MP4／SQLite→Telegram outbound queue 已接線；真正 inbound webhook 未實作 |

## 本輪實際證據

- Dashboard 以 repository `.venv` 啟動，`0.0.0.0:8000` 的 `/healthz` 回傳 HTTP 200。
- 正式 go2rtc 保持 `127.0.0.1:1984`／`127.0.0.1:8554`，未暴露到區網。
- `/settings/source` 是來源與攝影機的統一頁面；共用導覽只渲染一份，包含「設定」與「維護」。
- `reports/phase3a-smoke/cat30.mp4` 真實本地端到端乾跑：CPU、YOLO11n、找到 2 隻貓、產生 1 個事件 MP4 並寫入 SQLite。
- 公開 YouTube 測試頁以固定 yt-dlp 解析並取得第一個來源影格；簽名媒體 URL 未寫入設定、資料庫或日誌。
- 完整 unittest：168 tests，全部通過。
- 變更範圍 Ruff 與 `python -m compileall -q src tools tests`：通過。
- 全 repository Ruff 仍有 9 個既有 `tools/phase3c5_xiaomi_trace.py` 問題；本輪未修改該 Xiaomi 診斷工具。
- 前端硬編碼本機 URL 掃描與秘密模式掃描：0 命中。

## 目前尚未宣稱完成

1. D-Link 真實 RTSP 路徑、帳密環境變數與家庭網路長時間穩定性。
2. MX250 實機推論後端、VRAM、CPU／記憶體與 12～24 小時壓力結果。
3. 真實 Telegram Bot／Chat ID 的 MP4／圖片傳送。
4. Telegram inbound webhook；目前是 outbound Bot API queue，反向 webhook 需要 HTTPS 公開入口。
5. Xiaomi P2P 影像與家庭攝影機身分／行為準確率。
6. Windows Private Profile 防火牆實機建立、iPhone Safari 登入與桌面 Tray 視覺驗收。

## 下一個最小操作

使用者在 Dashboard 登入後，先進入「設定 → Telegram」閱讀四步驟引導，將 Token／Chat ID
安全保存到 Windows Credential Manager（不可用時由 DPAPI fallback 保存），啟用通知並重啟
Host；按測試通知確認文字 `ok=true` 與 `message_id`。接著使用可連線的真實 RTSP 與公開
YouTube 來源，分別驗證第一影格、YOLO cat、SQLite、JPEG／MP4 與 Telegram 文字／圖片／影片。
秘密不應貼入 Dashboard 報告、Git 或 Codex 對話。

## 本輪來源啟動修正（2026-08-03）

已修正來源設定頁的實際操作落差：舊版「保存後開始監控」只發送啟動請求，沒有先保存
目前表單，因此使用者未另外按保存時，Host 仍會從舊的 Mock 設定啟動。現在按鈕會先
保存 YouTube／RTSP 設定，再啟動同一個 `RepositoryCoreController` 與單一
`FfmpegMediaIngest`。頂端新增受 Session／CSRF 保護的「重新啟動 Host」，會以同一
repository `.venv` 重新載入最新程式與設定；Windows `execv` 可能配置新 PID，但不會
允許重疊 Host。

真實 8000 smoke 已驗證：臨時 Token 僅在測試程序環境使用，重啟 API 回覆 `restarting`，
重啟後 `/healthz` 仍為 HTTP 200，port owner 的 executable 為
`C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\.venv\Scripts\python.exe`。正式 Host
已恢復運行；完整 unittest 168 tests、變更範圍 Ruff 與 compileall 均通過。

目前 `config.json` 保留公開 YouTube smoke URL，Telegram 開關為 `false`，未保存任何
Token／Chat ID。這證明設定與啟動流程已修正，但不等於家庭 YouTube／D-Link RTSP 或真實
Telegram 傳送已完成；這些仍列為 `needs_real_data`。

## AI／行為與 Telegram Dashboard 設定接線（2026-08-04）

本輪補上前端到設定檔與 Host 的實際資料流：

- `/settings/ai` 不再是 placeholder；表單透過受保護的 `/api/v1/settings` 讀寫
  `dashboard_settings`，並驗證信心門檻、AI／行為採樣 FPS、冷卻、事件前後錄與 ring
  retention／容量範圍。
- Host 啟動會載入這些非秘密值，傳給 `RepositoryCoreController`；保存後不在執行中的
  ingest 上重建 FFmpeg，而是明確要求安全重新啟動，避免中斷 YouTube／RTSP 主路徑。
- `/settings/telegram` 保存非秘密 `telegram.enabled`，並新增四步驟引導、Bot Token／Chat ID
  欄位與「安全保存／清除憑證」操作。秘密寫入 Windows Credential Manager，Credential
  Manager 不可用時使用 DPAPI；頁面只顯示是否已設定，不回填秘密。測試按鈕已接到核心 callback。
- 行為與個體辨識仍是 `experimental`；這次接通參數保存不等於家庭實拍準確率驗收，
  Telegram inbound webhook 也仍未實作。

最後狀態快照（2026-08-04）：8000 owner 為 PID 29156、repository `.venv` 的單一
`tools\run_host.py`，其子程序只有一個 repository FFmpeg producer；`latest.jpg` 在最後檢查時
仍持續更新（約 2.8 秒前）。公開測試素材為兔子影片，這只證明
YouTube→FFmpeg→共享影格快取鏈路有影像，不代表貓咪辨識或家庭來源已驗收。

## 可攜式 runtime 準備進度（2026-09-21）

- `tools/prepare_portable_runtimes.py` 以固定官方 URL 與 SHA-256 取得 Python 3.13.15
  embedded ZIP 與 Node 22.23.2 Windows x64 ZIP；官方版本執行檔已在
  `runtime/python`／`vendor/javascript` 驗證，兩目錄不提交 Git。
- Python embedded 的 `python313._pth` 已啟用內附 `Lib/site-packages`，必要套件已安裝；
  建置器會用內附 Python 實際匯入檢查，缺少套件時拒絕建置。
- `dist/MeowKeeperPlus-portable` 已產出並完成本地 CPU 真實推論；已有可回退的排程註冊腳本，
  實機來源、Telegram、MX250 與長時間運作仍未通過發布閘門。
- 專案 `.venv` 內的 pip 已以 PyPI SHA-256 驗證 wheel 升級為 25.3（未修改系統 Python）；
  cp313 Windows wheel metadata 已可解析，但整套依賴安裝與整包啟動仍未完成。
- 2026-09-21 全部 unittest 194 個通過；全 repository Ruff 與全部
  `src/tools/tests` 語法編譯通過。
- 最終可攜包已重建為 18,656 個檔案；包內嵌入式 Python 搭配固定版
  yt-dlp／yt-dlp-ejs、Node、FFmpeg 與 YOLO11n，已以公開 YouTube 頁面取得真實影格、
  完成 CPU 推論，並由同一 ingest 產生 262,144 bytes MPEG-TS 片段。
  結束後無殘留包內 yt-dlp／FFmpeg 程序；Telegram 未啟用，沒有對外傳送。

## 2026-09-23 Windows 目前使用者實裝

- canonical `dist/MeowKeeperPlus-portable` 已重建為 18,657 個 manifest 檔案；模型 SHA-256
  為 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`，完整性與私人資料
  檢查通過。
- 實際安裝到 `%LOCALAPPDATA%\MeowKeeperPlus\App`，狀態資料位於獨立
  `%LOCALAPPDATA%\MeowKeeperPlus\Data`；最終使用者不需 Python、venv、pip、FFmpeg 或模型下載。
- 這台電腦拒絕標準使用者建立 Task Scheduler 工作，因此 `-CurrentUser` 模式改用目前使用者
  Startup CMD；系統級 ProgramData／SYSTEM 排程模式仍保留，且仍需使用者核准 UAC。
- 安裝後真實驗證：Dashboard `/healthz=200`、Host=1、Tray=1，兩者均使用安裝包 embedded
  `pythonw.exe`；重複執行兩個 Startup CMD 後 PID 不變，單一實例成立。
- lock metadata 已移至 `Data\locks`；App 目錄不再產生 runtime `data/`，運行中重新驗證
  build manifest 仍 `passed`。
- 已修正三個只有真實安裝才會暴露的問題：複製前以不存在目標產生 XML、將排程不存在視為
  致命錯誤，以及立即啟動繼承來源發布包工作目錄。
- 目前使用者模式只在 Windows 登入後運作，不能視為開機前無人登入的 24H SYSTEM 驗收。
  真實 Telegram、MX250、家庭 RTSP／YouTube 與 12–24 小時壓力仍維持待驗證。
- 安裝包內建 runtime 已通過本地貓咪影片的 YOLO→SQLite→MP4 閉環：CPU 偵測到 2 隻貓，
  2 筆事件為 `completed`，產生 2 個 FFprobe 可讀的 H.264 640×480、14.2 秒 MP4；Telegram
  明確停用且 attempts=0。此結果修正了 stream-copy 長 GOP 最後分段的索引時間窗問題，
  但不取代真實家庭來源與 Telegram 驗收。
- 首次設定未完成時，Dashboard 保護頁現在一律導向 localhost 專用 `/setup`；未登入 API 維持
  HTTP 401。路由回歸後完整測試為 198 項、Ruff 與 compileall 通過；發布包 `.cmd` 安裝入口的
  PowerShell 5.1 `-WhatIf` 及目前使用者升級均成功，更新後 Dashboard health=200、Host=1、Tray=1。

## 2026-09-23 YouTube→YOLO→事件 MP4 進度

- 真實 YouTube 解析成功（固定 yt-dlp 2026.7.4、yt-dlp-ejs 0.8.0、Node、FFmpeg／FFprobe 均可用），來源 metadata 為 VP9 360p。舊 MPEG-TS stream-copy 對 VP9 只留下 data stream；YOLO JPEG 偵測已成功，但 SQLite 事件為 `failed_with_snapshot`，MP4 未產生。
- 已修正 repository 媒體 ingest：已知 H.264／H.265 保留零重編碼快取；不相容 codec 用 640 寬、最高 5 FPS H.264 轉碼，並把 keyframe 間距限制到分段時長，避免再將無影像 TS 視為成功。
- 修正後 100 影格真實 YouTube 測試：YOLO CPU 偵測 1 隻貓、confidence 0.57；SQLite event `completed`；FFprobe 驗證 MP4 H.264 640×360、5 FPS、10 秒、447,760 bytes；Telegram disabled，輸出檔 signed URL marker 0 命中。
- 局部回歸：Phase 2 21 tests、YouTube adapter 6 tests、media pipeline 12 tests 通過；變更範圍 Ruff／compileall 通過。Luna Max 受限委派先檢視 smoke 命令並執行新增的單一回歸測試，主代理獨立重跑並完成真實媒體驗收；平台沒有額外模型採樣回執。
- 這是當時尚未重建／升級到 `%LOCALAPPDATA%\MeowKeeperPlus\App` 的狀態；r2 安裝後狀態見下一節。首次 Dashboard 管理員設定仍未完成。維持 `MVP_CODE_COMPLETE / MVP_REAL_E2E_PENDING`：真實 RTSP、Telegram 外送、MX250、直播重連與長時間家庭驗收未通過。

## 2026-09-23 最終 r2 安裝與 YouTube 閉環狀態

- 完成 VP9→H.264 ring cache 修正及 session 零位元組尾段清理。最終全套 unittest 200 項通過、全 repository Ruff 通過、`compileall` 通過。
- r2 可攜包 manifest 驗證通過（18,657 files；模型 SHA-256 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`；`private_data_present=false`），並已升級至 `%LOCALAPPDATA%\MeowKeeperPlus\App`。使用者資料仍獨立保存在 `%LOCALAPPDATA%\MeowKeeperPlus\Data`；r1 發布包保留作回退。
- 安裝後實際狀態：Host PID 20216、Tray PID 20600、go2rtc=0；Host／Tray executable 均為安裝包 `runtime\python\pythonw.exe`。Dashboard 僅監聽 `127.0.0.1:8000`；`/healthz=200`、`/setup=200`、`/api/v1/auth/status` 為 `configured=false, authenticated=false`。安裝器驗證 Host=1、Tray=1、所有 Python 皆來自 embedded runtime。啟動方式是目前使用者登入後 Startup link，非登入前的 SYSTEM 24H 服務。
- r2 可攜包真實 YouTube smoke：公開 360p VP9 素材、100 影格、YOLO11n CPU 找到 1 隻貓（0.57）；SQLite `completed`，H.264 640×360、5 FPS、7 秒 MP4（363,882 bytes）。Telegram disabled、傳送 0 次；signed URL 掃描 0 命中、清理後零位元組 ring 段 0 個。
- **仍待使用者操作／實機驗收：**先在本機 `/setup` 建立首位管理員，再於 Dashboard 安全設定 Telegram Bot Token 與 Chat ID，才能驗證真實 message_id；之後分別以真實 RTSP 與 YouTube 含貓來源驗收通知閉環。MX250、YouTube live 重連與 12–24 小時家庭環境仍未驗收。
- 專案狀態維持 `MVP_CODE_COMPLETE / MVP_REAL_E2E_PENDING`，不可宣稱已可對外發布或已通過家庭 24H 驗收。

## 2026-09-23 SYSTEM 開機啟動預演

- r2 system-mode `-WhatIf -SkipStart` 通過：目標 `%ProgramData%\MeowKeeperPlus\App` 與 `Data`、Task Scheduler Core／Tray 定義正確，且 `system_changed=false`。
- 正式系統安裝遇到 Windows `RunAs` UAC 時回報操作已取消；未建立 ProgramData App/Data 或 SYSTEM 排程。未移除、停止或改動目前使用者版；重新檢查確認 Host PID 20216、Tray PID 20600、Dashboard localhost 8000 仍正常，go2rtc 1984／RTSP 8554 沒有 listener。
- `/healthz=200`、`/setup=200`、未登入 `/api/v1/status=401`、auth `configured=false`；首次設定專用回歸 7 項全部通過。此可證明目前使用者模式的登入保護與 setup 路由，不代表 Telegram、SYSTEM 開機啟動或家庭來源驗收。
- 對使用者先前提供的兩個攝影機 LAN 位址做唯讀 TCP/554 探測，兩者均為不可達；未猜 RTSP 路徑或送入帳密。這只表示目前不能直接以 LAN TCP/554 驗收，不推翻先前 `.55` 的後端 MIoT／SG 雲端串流紀錄，也不代表已在目前安裝版重新接通。
- 因 SYSTEM 安裝沒有獲得 Windows UAC 核准，24H 開機前監控仍 `not_checked`；不會藉由其他機制繞過 UAC。`MVP_CODE_COMPLETE / MVP_REAL_E2E_PENDING` 維持不變。

## 2026-09-23 安裝版 YouTube 現行直播複驗

- 使用 Caring for Cats 官方 24 小時 Kitty Cam 頁面目前嵌入的公開直播（`48G0X3sPECo`），而非已失效的搜尋結果；r2 安裝版 yt-dlp 解析為 live、360p H.264／AAC。
- 一次 bounded live YOLO run 正常消耗 8 張真實影格、CPU backend、事件數 0。另一次以 event-video 模式讀取 30 張分析影格，建立 3 個有效 MPEG-TS ring 分段；FFprobe 驗證其中一段 H.264 640×360，約 5.016 秒。SQLite event rows=0；沒有啟用 Telegram。
- 此新增實測擴大了 YouTube 正例一般影片驗收到現行直播的來源覆蓋，但不等於直播斷線重連、持續監控、直播中正例事件 MP4、RTSP 或真實 Telegram 驗收。狀態仍為 `MVP_CODE_COMPLETE / MVP_REAL_E2E_PENDING`。

## 2026-09-23 r3 可攜包與 SYSTEM 遷移防重複保護

- SYSTEM 安裝器新增 fail-closed preflight：若目前使用者仍有舊 App manifest 或精確的 User Core／Tray Startup 啟動檔，會在 UAC 前停止，不自動終止程序、刪除啟動檔或移動資料；繁體中文提示使用者先以目前使用者權限執行 `Uninstall-MeowKeeperPlus.cmd -CurrentUser`，預設保留 Data。
- `-WhatIf` 命中舊安裝時回報 `status=blocked`、標記路徑及 `system_changed=false`；未命中時沿用原先發布包與 Task XML 驗證流程。
- 新可攜包建置於 `dist/MeowKeeperPlus-portable-2026-09-23-r3`；內附 Python 驗證器通過，manifest 共 18,657 個檔案，模型 SHA-256 為 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`，`private_data_present=false`；包內安裝器 hash 與 repository 來源一致。
- Windows PowerShell 5.1 parser、套件 `-WhatIf` 與攔截 `Start-Process` 的 UAC 前 guard 檢查均通過。實際安裝尚未執行；目前 r2 Host PID 20216、Tray PID 20600 與 localhost Dashboard 未改動，SYSTEM Tasks／ProgramData 安裝不存在。
- 舊使用者 Data 唯讀盤點：`config.json`、`web_admin.json`、事件及 ring 目錄不存在；SQLite 11 張表中只有 `schema_meta` 有 1 筆，其餘皆為 0。解除安裝會保留該 Data 目錄，但 SYSTEM 安裝不會自動搬移；目前未發現需遷移的設定或事件資料。
- 後續 SYSTEM 遷移仍需使用者先移除目前使用者模式 App（資料保留），再從 r3 發起安裝並核准 Windows UAC。這不是已完成的開機前 24H 實機驗收；真實 RTSP、Telegram、MX250 與 12–24 小時測試仍待完成，專案狀態維持 `MVP_CODE_COMPLETE / MVP_REAL_E2E_PENDING`。

## 2026-09-23 r4 升級程序安全修正

- `scripts/Install-MeowKeeperPlus.ps1 -CurrentUser -Upgrade` 現在先驗證舊 App 固定路徑與 manifest，再以 embedded `pythonw.exe` 的精確路徑及 Host／Tray 腳本 argv 前綴識別程序；命令列缺失、路徑不符、handle／建立時間重查失敗、停止失敗、共用 15 秒期限逾時或重掃仍有程序時，會在改動 Startup 或移除舊 App 前中止。
- Windows PowerShell 5.1 AST 語法錯誤為 0；隔離記憶體測試驗證 Host／Tray 精確命中、後續參數中的相似路徑不命中、程序資訊不足與 CIM 讀取失敗均 fail-closed，且測試沒有停止真實 MeowKeeper+ 或其他 Python 程序。未以真實執行中的 Host 驗證成功終止路徑。
- 新建 `dist/MeowKeeperPlus-portable-2026-09-23-r4`：內附 verifier 通過、18,657 個檔案、模型 SHA-256 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`、`private_data_present=false`；包內安裝器 SHA-256 與來源一致。包內 system-mode `-WhatIf` 因目前使用者安裝標記回報 `status=blocked`、`system_changed=false`，沒有進入 UAC 或修改系統工作。
- 本輪重新唯讀確認：目前使用者安裝仍為 r2；Host PID 20216、Tray PID 20600 均使用該安裝的 embedded `pythonw.exe`。Dashboard `127.0.0.1:8000` 由 Host 持有，`/healthz=200`、`/setup=200`；`/api/v1/auth/status` 回報 `configured=false`、`authenticated=false`。go2rtc 程序與 1984／8554 listener 均為 0。r4 尚未安裝，使用者資料及 Startup 未改動。
- **接續 gate：**首次管理員建立與 Telegram 真實 `message_id`、家庭 RTSP／YouTube 含貓完整通知、MX250／12–24 小時穩定性、SYSTEM UAC／重開機仍待驗收。發布狀態維持 `MVP_CODE_COMPLETE / MVP_REAL_E2E_PENDING`。
