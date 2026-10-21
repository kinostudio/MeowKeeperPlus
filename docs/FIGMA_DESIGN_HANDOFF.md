# MeowKeeper+｜完整應用功能與 Dashboard 設計交接

盤點日期：2026-10-12。供 Figma／Figma Make／設計師重新設計使用。

後續 r91 更新：AI 頁已加入自動／CPU／Intel 內顯／NVIDIA 外顯選擇，保存後重啟套用。下文 r90 基線中「GPU selector 尚未實作」的描述不再適用 r91；詳見 [r91 接線與安全邊界](R91_DEVICE_SELECTION_TRAY_AUTH.md)。

## 0. 文件用途與事實邊界

本文件盤點目前 repository 的 Jinja 頁面、前端 JavaScript、API routes、Tray 與 Telegram 流程，設計基線為 r90 原始碼；不是目前使用者電腦安裝版本或真實攝影機驗收結果。沒有取得本轮登入後的瀏覽器截圖，因此現況視覺依 CSS／模板描述，不宣稱已逐頁目視驗收。

標記：**現有 UI**＝有頁面／元件與呼叫接線；**條件功能**＝受裝置、權限、秘密或執行環境限制；**實驗性**＝身分／行為未完成家庭準確率驗收；**建議設計**＝尚未實作，不得當成現有功能。API 存在不等於已有可操作介面。

搭配 [Figma 生成提示詞](FIGMA_GENERATION_BRIEF.md)、[設計 token](figma/design_tokens.json) 使用。本文件不授權修改登入、來源架構、防火牆或驅動，也不要求上傳私人資料。

## 1. 產品與使用者

品牌：喵喵守護者+ / MeowKeeper+。
副標：MeowKeeper+ — Local AI Pet Guardian；中文「本地 AI 全天候守護每一隻貓咪」。

這是一套在 Windows 電腦背景運行的本地寵物監控工具。把一個影音來源接入後，分析是否有貓、保存事件前後影片、透過 Telegram 通知；進階模式可追蹤多貓、提出個體與行為候選，使用者自願回報修正。

不是雲端多攝影機 NVR，不是醫療診斷，也不是手機直接連小米雲端。多台攝影機可以登錄／配對，但目前核心僅接入一個來源，不應畫成同時監控九宮格。

主要使用者：

- 普通飼主：想知道有無持續畫面、貓是否出現、通知能否送達；不熟悉 RTSP、codec、Port 或 embedding。
- 家庭通知接收者：透過 Telegram 收到影片、查看狀態、自行靜音／選擇參與回饋。
- 管理者：設定攝影機、保存憑證、登錄寵物、備份與處理網路問題。
- 維護者：展開技術狀態、日誌、硬體負載與診斷，不應要求普通使用者先讀 JSON。

典型情境：在家查看、離家看通知、用手機看 Dashboard、另一住處中繼接入、電腦未登入仍由 SYSTEM Core 監控、可攜版移到另一台電腦。

## 2. 全應用概念模型（不能被重設計改掉）

```text
單一來源 → 本地 ingest／壓縮環形快取
              ├─ 最新影格 → 共用 Dashboard 預覽
              ├─ 找貓／動作行為 → SQLite 事件 → MP4／JPEG → Telegram
              └─ 背景個體辨識 → 補寫身分 → 統計
Dashboard／Tray／Telegram 指令 → 同一背景核心（不是各開一條攝影機）
```

- 關閉網頁、登出，不等於停止監控；停止監控、重啟監控、重啟整個服務、完整關閉是四種不同操作。
- 快取與錄影優先於 AI、通知及身分辨識；未知身分不能使事件丟失。
- 來源解析度、預覽解析度與更新率、AI 張量尺寸與分析 FPS 分開；調 AI 不改攝影機原始畫質。
- 憑證、完整私人來源網址不回填到一般頁面／日誌；事件影像與備份也屬私人資料。
- go2rtc 管理 API 本機限定；Dashboard 的區網存取仍需登入。裸 RTSP LAN 開放是另獲授權的網路行為，不由新 UI 預設開啟。
- Windows 包含 Python、FFmpeg、模型；最終使用者不需要 pip／venv。升級由使用者自行執行，UAC 不可偽裝自動核准。

## 3. 現有頁面與導覽完整清單

| 區域 | 路徑 | 現有頁面／模板 | 用途與邊界 |
|---|---|---|---|
| 首次使用 | `/setup` | setup.html | 建立本機管理者；只在尚未設定時使用 |
| 登入 | `/login` | login.html | 本機帳號登入，不是小米／Telegram 登入 |
| 守護日常 | `/`、`/dashboard` | dashboard.html | 同一總覽，不做成兩頁 |
| 守護日常 | `/live` | live.html | 預覽、畫質、保存、PTZ、隱私與位置圖 |
| 守護日常 | `/events` | events.html + event-list.js | 最近事件與受保護影片播放 |
| 守護日常 | `/analytics` | analytics.html | 資源趨勢與實驗性行為統計 |
| 設定 | `/settings/source` | settings.html/source.js | 單一來源保存／啟動與小米入口 |
| 設定子頁 | `/cameras/xiaomi/setup` | xiaomi_setup.html | 登入、區域、裝置、測試、接入與中繼配對 |
| 舊入口 | `/cameras` | redirect | 目前轉到 `/settings/source`；不新增重複導覽 |
| 設定 | `/settings/ai` | settings.html/ai.js/zones.js | 分析、快取、回饋統計與區域 |
| 設定 | `/settings/pets` | settings.html/pets.js | 登錄／編輯／追加照片／建立特徵 |
| 設定 | `/settings/telegram` | settings.html/telegram.js | 憑證、對象、啟用、測試 |
| 設定 | `/settings/tailscale` | settings.html/tailscale.js | 私人網路狀態與管理頁連結 |
| 維護 | `/diagnostics` | diagnostics.html | 登入後查看遮罩診斷，目前主體是 JSON |
| 維護 | `/logs` | logs.html | 最近 200 筆遮罩 Host 訊息 |
| 維護 | `/settings/advanced` | settings.html/settings.js | 完整備份、區網、Port、防火牆 |

現有側欄為三組：「守護日常」「設定」「維護」。共用 base.html 的頂列有品牌、副標、重新啟動服務、行動導覽收合；底部有術語小指南與「關閉網頁服務仍運作」說明。

**目前 base.html 沒有可見的登出／帳戶選單**，雖然有 logout API。新設計可提出帳戶選單，但必須標記需要接線。全局 AI backend 選擇器、任意 GPU 切換也是建議，不是現有欄位。

## 4. 逐頁功能／內容／交互規格

### P01 首次設定與 P02 登入

現有欄位：管理者帳號（首次 3～80 字元）、密碼（首次至少 12 字元）、確認密碼。登入只輸入帳號／密碼。首次建立後轉登入，登入成功轉總覽。

設計所有狀態：未填、密碼不一致、提交中、帳密錯誤、嘗試過多（429）、服務不可達、已建立（409）、成功跳轉。密碼支援貼上／password manager，不新增自製 CAPTCHA。Session 過期導向登入；保護頁面不可用「空白成功」掩飾。

版面布局建議：窄版單卡，清楚標示「這台電腦的管理者帳號」。流程說明與品牌次要；不要在總覽再放登入欄。首次設定密碼最低 12 與備份密碼最低 6 必須區分。

### P03 總覽

現有主要卡：①監控與辨識 ②攝影機畫面 ③手機通知。每 5 秒讀取狀態。現有快速入口：即時畫面、事件影片、來源與攝影機。

現有內容／操作：

- 最近事件列表，連往全部事件。
- 開始監控、重新讀取狀態、擷取畫面、取得最近 5 秒。
- 折疊操作：重新啟動監控、停止監控（確認）、模擬事件（不是真實模型事件）。
- 技術折疊：FFmpeg、AI backend、環形快取、Tailscale、SQLite、應用程序群 CPU／RAM、整機 CPU／RAM、磁碟剩餘空間、可用 Dashboard 網址。

版面布局建議：第一屏回答「有新畫面嗎／正在辨識嗎／通知可用嗎」，最近事件次之；技術資料放抽屜。狀態要有最後更新時間，不以 Healthy 或 running 代替取流成功；82.9 GiB 等剩餘量搭配實際門檻，不能無條件用危險色製造「磁碟不足」錯覺。

### P04 即時畫面

現有 preview 主區：最新 JPEG、等待影格空狀態、消息、來源原始規格（最近驗證）、preview 尺寸／更新 FPS、AI 尺寸／FPS。多瀏覽器共用影格，不另開 producer。分頁隱藏時降低／停止頁面更新。

| 操作 | 現有語意 | 必須呈現 |
|---|---|---|
| 全螢幕 | 擴展預覽區 | 無影格停用；鍵盤與退出入口 |
| 截圖 | 原始快取截圖優先；失敗才預覽 | 實際尺寸、是否降級、保存結果 |
| 保存前 15 秒 | 按下之前的環形快取 | 待組片／成功／不足15秒／失敗、實際長度 |
| 重新讀取 | 讀取頁面／服務狀態 | 不代表重新連攝影機 |
| ↑↓←→／■ | 相對短步方向與停止 | 裝置可控才顯示；命令／停止確認或失敗 |
| 畫質 | 替換既有 Xiaomi stream | 套用中短暫中斷、實測規格，不增加連線 |
| 讀取位置 | 本次裝置座標查詢 | 真實成功與未知分開，不讓讀取按鈕造成移動 |
| 位置隱私 | 保存指定位置、hide/show | 能力與讀回驗證 gate、原位置恢復結果 |
| 隱私待機 | MIoT 原生開關 | 會中斷影像、需回讀，不是斷電或實體遮罩 |

手動檔案在 Host Data 主目錄 `photo/`、`video/`；安裝版 Data 與 App 分離，可攜版使用 data。前端只顯示安全路徑資訊，不輸出私人來源連線。

畫質選項完整：subtype 1、2、3、auto、sd、hd、0、4、5。預設 1；code 不等於固定像素。通常 SD 低、HD 高；C701 3 的 4K 是歷史／社群與特定韌體案例，不保證每台。選項顯示「實測：848×480」或「未驗證」，不寫無條件「4K」。

位置圖：目前為 0～101 裝置座標的線性示意點；非角度、平面圖或真實視野。只有本次有效讀回才有圖點；切換裝置、移動、hide/show、讀回失敗後清除。沒有 absolute_move 驗證不可讓使用者拖圖直接移動。

鍵盤：方向鍵短步，Esc 停止；輸入框／選單聚焦、修飾鍵、分頁隱藏不觸發鏡頭移動。

版面布局建議：影片占主區，影片旁／下方一列放截圖、15秒、全螢幕與清楚的停止鍵。次區分「畫質」「方向」「隱私」；大段協定資訊進展開面板。全螢幕保留必要控制，不把長設定表擠在影片上。手機不要讓 PTZ 蓋住重要影像。

### P05 事件影片

目前讀最近 50 件（limit=50/offset=0），可見欄位：事件類型、發生時間、pet_id／尚未確認、模型分數（非準確率）、實際片長、Telegram 狀態、是否有影片。影片存在才顯示「播放事件影片」；沒有片可能尚未完成或已清理，不等於資料庫事件消失。

必備狀態：載入、空、登入過期、讀取失敗、組片中、有效影片、影片已到期、通知等待／已送／失敗／關閉。

目前沒有完整可見的篩選器、時間線、分頁控制、批次刪除、事件詳情 drawer 或 Web 標註 editor。API 有部分事件詳情／標註能力，但這些新元件要標「建議接線」。設計可規劃，但不將「人工確定身分」混為「模型候選」。

### P06 資源趨勢與行為統計

資源現有：期間 1h／24h（預設）／7d／30d／90d、更新按鈕、Canvas 折線、下方可讀數值表。

四組圖：整機／應用 CPU%；整機／應用 RAM%；應用 RSS MiB；程式＋模型／Data＋影片／總占用 GiB。每分鐘採樣、保留90天；磁碟大小約每15分鐘計算。不開網頁仍由 Host 記錄；停機缺口不補造、不用折線連過缺失。長期間分桶曲線是分段均值，不是瞬間峰值。

Telegram /status 的一小時原始採樣平均／峰值與此圖分桶統計是不同語意。GPU、溫度、磁碟 I/O 已有狀態資料，不代表目前 analytics 已有對應曲線；新增圖屬建議接線。

行為現有：開始／結束日期（包含結束當天）、查詢、按個體／未知分組。表：行為、累計秒數、期間開始次數、相交事件數、平均觀察秒數、占日曆時間；顯示未結束／無效區間。候選區間重疊時不計百分比。沒事件不是睡眠／沒活動。

版面布局建議：拆「電腦資源」「寵物行為」tabs，共用日期區但語意獨立。不要把玩耍和奔跑加成互斥總時數。圓餅圖／活動熱力圖／健康分數沒有現成完整 UI，須提案而非現有宣稱。

### P07 來源與攝影機

欄位：來源type（YouTube／RTSP／本機影片／Mock）、頁面 URL／檔案路徑、YouTube max_height（360/480/720預設/1080）、video=true、audio=false。

操作：保存來源、保存後開始監控、停止監控、小米精靈入口。保存≠開始，非同步 starting≠成功。Mock 無真實攝影機，不設真實接通徽章。

YouTube 保存原分享 URL，不保存解析的媒體 URL、Header/Cookie。公開／有連結的非公開影片可嘗試；私人、會員、年齡／DRM 不支援，不放 Cookie 或帳號登入入口。audio 保留不等於聲音 AI。

現有 RTSP 表單拒絕含帳密 URL，沒有完整安全帳密編輯器；不能為新設計假設已支援。未實作欄位可提案，但不要求把密碼塞進一般 URL。

版面布局建議：來源類型選擇卡 → 對應欄位 → 目前與待套用來源比較 → 保存／套用結果。Xiaomi 在此作專用入口，不另做與「攝影機」重複的第一層頁。

### P08 小米設定子精靈

現有主流程：準備工具 → 官方本機頁登入 → 載入與 Region Discovery → SD有效影格測試 → 接入唯一監控來源。

主操作：準備 go2rtc、開啟登入、明確重試、擷取安全登入診斷、重新載入並確認區域；有冷卻、stage、遮罩 LoginError／HTTP 摘要。

裝置卡：顯示名稱、model、region／candidate、online／selected／monitoring_active、影格測試說明與已驗證codec/尺寸/FPS。操作：測試畫質（已接入時為讀取目前畫質）、接入 MeowKeeper+、唯讀連線／防火牆診斷；支援時讀方向狀態與方向／停止。

遠端中繼配對目前 UI 僅為 C701 型號建立：展開配對說明、10分鐘有效配對碼、驗證保存、遠端接入、已配對狀態。不能將此介面畫成所有攝影機皆已支援，也不能只靠 Tailscale IP 自動認定攝影機可達。

本機登入成功不等於有影格，候選sg不等於已驗證。70016 不直接說密碼錯；UDP timeout／解碼錯分層。診斷先提供下一步，不要求無限重新登入／重啟。防火牆診斷不會自動修復；修復要明確授權／UAC。

目前精靈部分文字仍寫「官方 go2rtc」，但發布包可啟用已驗證 PTZ 實驗版。設計要讀 runtime backend 標籤，不把固定文案當成現況。

版面布局建議：進度 stepper＋單一當前下一步，登入診斷折疊，裝置清單用卡片。每張卡明確區分「本機直連」「已配對遠端中繼」與目前使用模式；不要要求使用者猜 stream 名稱或 RTSP。

### P09 AI 與行為參數

| 現有欄位 | UI 範圍／選項 | 說明 |
|---|---|---|
| 分析模式 | 基本找貓／V0.2 | 同一 YOLO cat-only；V0.2 加追蹤／規則／背景身分，實驗性 |
| 信心門檻 | 0～1，step .01 | 初始 .45；不是百分比準確率 |
| 分析 FPS | 1～5 | 設定上限，實際可被負載調低 |
| 分析長邊 | 640/960/1280/1920 | 模型張量大小，不改來源錄影 |
| 行為採樣 FPS | 1～5 | V0.2 使用，非基本找貓 |
| 預覽最大寬 | 640/1280/1920 | 預設1920；受來源上限限制 |
| 身分保留寬 | 640/960/1280 | 預設1280；crop不足96×96跳過，不阻塞主線 |
| 冷卻秒 | 0～86400 | 初始180；0可能洗版 |
| 事件預錄 | 0～120秒 | 初始5；不能超過快取長度 |
| 事件後錄 | 0～180秒 | 初始10；等待後錄完成再送片 |
| 環形快取 | 10～120秒 | 初始60；非事件保存天數 |
| 快取容量 | 16～4096 MB | 初始512；容量先滿時預錄可變短 |

操作：保存／恢復預設、低頻重試 AI；顯示實際backend、effective_size/FPS、AI PID、load scope／temperature／回退原因。現有畫面沒有 GPU 手動選擇 dropdown。

初始負載目標40%（非硬上限）；CPU85°C持續降級、95°C暫停；GPU80°C持續降級、85°C暫停。是保守策略，非每台已驗收／安全保證；讀不到溫度顯示 unavailable。

人工回饋區：已審核數／爭議／分桶 precision／Wilson95%區間；不代表 recall 或整體 accuracy，不直接調 YOLO 權重。

區域表單：名稱、用途（food/water/bed/litter_box/play_area/excluded）、X/Y/寬/高百分比、加入待保存清單、移除、保存、重新載入；來源綁定，保存後重啟監控，鏡頭轉向需重設。**目前不是影像拖曳框選**；可建議新版用 frame上畫ROI＋數值備援，需實作。

版面布局建議：基本（模式／敏感度／FPS）→ 事件錄影 → 進階效能 → 實驗性身分／行為／區域 → 回饋統計；顯示「設定值 vs 實際值」，不要所有數字混一張表。保存後需重啟項目附就地提示。

### P10 寵物身分登錄

新增：名稱（80字元）、固定ID可留白（英數_-，64）、毛色／花紋各200字元、JPG多選（每張8MB），登錄並建立特徵、刷新。

編輯：名稱、描述、enabled、追加照片、保存／取消；卡片顯示名稱、描述、ID、參考照片數、特徵數、建立狀態／說明。照片多於特徵且不在queued/running才顯示重試。建立期間每2.5秒檢查，不停止監控。

特徵是本機 DINOv2 參考向量，不是照片直接重訓 YOLO；照片已存≠features已建；unavailable 要有可恢復下一步。參考照片宜3～10張不同角度，越多不保證更準。毛色描述不等於模型已學會該名稱。

目前沒有單張照片管理／刪除／裁切 editor 或刪除寵物的可見按鈕；名稱與照片編輯已實裝。設計可提案縮圖相簿但不能默認已有安全圖片endpoint；勿用私人照片做Figma素材。

版面布局建議：寵物卡＋新增CTA、編輯drawer、照片上傳批次結果、建立特徵進度。保留一個明確「實驗性」標籤，不讓每欄重複警告造成閱讀疲勞。

### P11 Telegram

現有欄位／操作：事件enabled、Bot Token密碼欄（永不回填）、多收件者列（別名、Chat ID、enabled、移除）、新增／保存對象；啟動健康通知checkbox；安全保存／清除憑證、僅保存設定、啟用並重啟Host、測試文字。

狀態分開：憑證已設定、設定enabled、runtime enabled、selected數、測試成功／錯誤。別名本機使用，Chat ID遮罩；不能用 @個人username代替數字ID。/start只提供自己的ID，不自動授權。

目前UI有測試文字，**沒有獨立測試JPEG/MP4按鈕**；後端實際通知支援圖片／影片，不應設計成這兩個測試已接線。群組可作通知目的地，但逐軌跡人工回饋仍受私人Chat／操作者／receipt驗證限制。

版面布局建議：四步憑證→通知對象→啟用→測試，步驟內明確已完成與待套用；減少5個同權重按鈕。已保存token顯示安全badge，不把空密碼欄當作沒設定。

### P12 Tailscale

現有四項：登入狀態、Tailscale IP、遠端控制台可用狀態、安全可用網址；檢查按鈕、官方Admin連結。非VPN管理器，不代登入、不改網卡／router／路由。Subnet可達≠可取Xiaomi影格；區分Dashboard遠端瀏覽與攝影機來源中繼。

版面布局建議：概念分「遠端看控制台」與「另一住處攝影機」；檢查結果卡及具體下一步。候選網址不等於已驗證可用。沒有CLI、未登入、已登入但未可用、available、檢查失敗各有狀態。

### P13 診斷、P14 日誌、P15 進階

診斷現有遮罩JSON顯示；export API存在，頁面沒有獨立匯出CTA（Tray有）。建議設計健康摘要＋問題分層＋技術JSON／安全下載；新增CTA需接線。

日誌現有最近200筆、5秒刷新、hidden不刷新、登入保護、空／錯誤；沒有可見搜尋、層級filter、完整檔下載。這些可提案，不可當現有能力。

進階現有完整加密ZIP：備份密碼≥6、ZIP選檔、匯出／匯入、完整性驗證、匯入確認／提示重啟。AES-GCM保護內部資料，不應宣稱所有ZIP工具可直接解密。包含settings、通知偏好、SQLite事件／回饋、寵物／特徵、完成影片／截圖與可供備份的本Host Telegram憑證；**不含Xiaomi Session、runtime、模型、ring cache**。環境變數提供的token不匯出。跨機可能需重新登入。

區網現有：LAN開關、Port1024～65535、保存／重啟提示、可用LAN網址、複製、PrivateDashboard防火牆建立／移除狀態。預設loopback；0.0.0.0不是網址。無admin時安全失敗；不包括Public、go2rtcAPI或任意port。小米UDP修復是另一套授權，不可混用一鍵「全開」。

版面布局建議：備份與還原分區，還原使用明確danger確認但不是紅色整頁；網路／防火牆單獨settings tab。不要重加已要求移除的「舊格式設定備份」UI。

## 5. Dashboard 之外的應用功能

### Windows Tray

完整12選項：開啟守護者、目前狀態、截圖入口、保存影片入口、小米設定、啟動背景核心、停止背景核心、重啟背景核心、事件資料夾、日誌、匯出診斷、完整關閉。

截圖／影片選單目前開啟live頁，不宣稱直接產生檔案。狀態／操作有Windows通知或對話框；勿擾可能隱藏通知。完整關閉停止本App核心／AI／媒體／Tray／Dashboard服務，保留資料；**不關閉瀏覽器分頁**、不停止其他專案。SYSTEM權限可能需UAC。r90修正原生menuowner／WM_NULL及popup期間延後更新，使用者筆電hover仍須人工驗收。

Figma交付需包含Tray icon三狀態（ready/waiting/error）與原生選單文字，不把WebPopover原型當真正Windows hover已解決。

### Telegram 互動

命令：/start、/snapshot、/live、/status、/today、/hide、/show、/reset、/home、/catnotify、/mute、/unmute、/feedback；行為依授權與capability，並非任何新Bot使用者可控制。

- /live為最近快取，預設前5秒，不是重新開始錄影。
- /status回報電腦／硬體與一小時採樣利用率、可得溫度、磁碟I/O、服務狀態與安全Dashboard網址。
- /mute N只靜音自己的貓咪通知，不停止AI或其他收件者；/feedback個人選擇是否參與。
- cat_detected影片與finalized行為摘要是不同觸發；一般逐貓圖片＋摘要＋按鈕在r89合為同則，長caption或圖片失敗才退回文字。
- 第一輪個體／兩隻／非貓依登錄寵物動態產生；複數貓分別回報行為與動作。Track編號只對應當次圖片，不是永久身分。
- 模型分數不是準確率；人工回饋保存不可覆寫歷史，特徵擴充不等於自動重訓YOLO。
- MP4優先H.264/yuv420p相容封裝、正確索引／metadata；HEVC等必要轉碼，保留原始片。sendVideo成功不是手錶播放驗收。

Figma可製作去識別示例訊息與按鈕，但不要以真實ChatID、token、攝影機截圖或家庭姓名為素材。原型不能改Telegram原生元件能力。

## 6. 現有視覺與新設計原理

### 已有視覺語言

米白／卡其／大地色：背景`#F5F0E7`、卡片`#FFFDF8`、文字`#392E25`、次文字`#66584B`、線`#D9CBB9`、主accent`#735338`、頂列`#493728`、側欄`#EEE5D7`；成功`#536647`、警告`#80551C`、錯誤`#9B4839`。現有meta theme-color仍是舊綠色，tokens以experience.css覆蓋後實際值為準；此差異需後續UI實作統一。

本機字型Segoe UI／Microsoft JhengHei／Noto Sans TC fallback；Figma可用Noto Sans TC呈現，runtime不因設計新增外部font CDN。現有側欄222px、頂列最小88px、内容max1440px、卡radius16、按鈕radius9／最小44px、輸入最小46px。這些是現況，不是禁止重新安排。

### 原理與原因

1. **先傳達守護結果，再顯示技術。** 普通飼主不應從FFmpeg/SQLite狀態猜監控是否成功。
2. **單一真實狀態與時間戳。** 顯示最近影格與最近更新；過期／未知不標綠，candidate不是verified。
3. **漸進揭露。** 基本設定先行，協定／JSON／感測器細節進details；關鍵限制仍在操作旁可見，不只hover。
4. **狀態與動作分離。** 保存、套用、重啟結果要能分辨；不要因收到202／starting就提示「已接通」。
5. **最小操作與明確主次。** 每區只一個主要CTA；危險操作遠離高頻按鈕，需確認，顯示影響範圍。
6. **條件能力。** PTZ／位置／中繼／原生開關由能力旗標決定；disabled附原因，不把所有型號畫成全功能。
7. **低負載視覺。** 不加入背景影片、大量blur／粒子、持續動畫；不以UI分頁新增ingest或大量輪詢。
8. **數據誠實。** 無溫度不是0°C；設定5FPS不是實測5；RAM RSS／整機RAM、磁碟占用／I/O分開。
9. **可及性。** 正文對比4.5:1目標、狀態不只顏色、focus可見、touch至少44px、reduced-motion、表單label與helper、非hover專屬提示。參考[WCAG2.2](https://www.w3.org/WAI/WCAG22/quickref/)；44px是本專案目標，不將其誤稱所有WCAG AA情境的統一門檻。

## 7. 建議新版資訊架構（提案，非現況）

保留三層語意但減少認知負擔：

```text
日常：總覽 / 即時畫面 / 事件 / 趨勢
管理：攝影機與來源 / 寵物 / 通知
系統：效能與錄影 / 遠端與網路 / 備份 / 診斷與日誌
```

來源頁含「一般來源」「小米裝置」tabs；小米精靈是drawer或子流程，不加重複攝影機入口。趨勢含資源／行為tabs。效能與錄影頁重組現有AI表單；設定URL可保留alias，不以設計擅自刪後端route。

新增帳戶／登出、ROI畫框、事件filter、GPU選擇、照片縮圖、Web回饋editor均列「需後端／前端實作」，不可把本輪設計交付當已完成。

### 核心版面布局參考

```text
桌面：左側持久導覽 | 頁名＋目前狀態＋低頻全局操作
                    三個主要守護狀態 → 主內容 → 折疊技術資料

即時：主影片（寬） | 當前來源＋接通狀態（窄）
      保存/全螢幕  | 畫質 → PTZ → 隱私 → 位置參考
      原始/預覽/AI三個規格清楚分欄；不疊滿影片

手機：品牌＋menu → 頁名/狀態 → 影片/主要操作
      卡片單欄 → 次要控制accordion；同源導航抽屜
```

## 8. Figma 必須交付的文件與元件

建議Figma pages：00_Readme、01_IA_Flows、02_Foundations、03_Components、04_Desktop、05_Mobile、06_States_Prototype、07_Handoff。

元件／variants：AppShell、SidebarGroup/NavItem、Topbar、StatusBadge、MonitoringCard、MetricCard、EventRow/Card、VideoPreview/Toolbar、PtzPad（含stop）、PositionReference、QualitySelect、DeviceCard、Stepper、FormField/Helper/Validation、SecretField/SavedIndicator、RecipientRow、PetCard/FeatureProgress、UploadResult、TrendChart/Legend/Table、EmptyState、ErrorState、InlineResult/Toast、ConfirmDialog、DetailsDrawer、BackupRestorePanel。

每個按鈕有default/hover/focus/pressed/disabled/loading；表單有empty/valid/invalid/saved_pending_apply。Status至少unknown/loading/ready/streaming/reconnecting/offline/disabled/unsupported/failed/stale/experimental。顯示詞用繁體中文，不依顏色猜狀態。

Frames：1440桌面、1280筆電、768平板、390手機、360小螢幕；這是交付尺寸，不是目前已通過所有瀏覽器。桌面12column、mobile4column、8px spacing scale、Auto Layout與constraints、文字wrap不寫死高度。表格窄版轉card或局部scroll，不能整頁橫向捲動。

至少12組狀態原型：首次setup、正常找貓、已登入但無來源、mock、來源重連、AI降級、Telegram已保存未啟用、通知失敗、身分unknown、features unavailable、PTZ不支援／timeout、位置已讀／失敗、影片已清理、備份匯入確認、LAN未啟用／候選URL、登入過期。

核心可點流程：setup→login→source→valid frame→monitor；Xiaomi→SD驗證→activate→live；Telegram→recipients→apply→test；pet→upload→features→edit/retry；event→view→回饋概念；privacy→confirm→resume→readback；backup→password→export／importconfirm；LAN→save→restart→newURL。

## 9. 文案與示例資料

示例僅使用「貓咪A」「貓咪B」「家人手機」、`camera-demo`等；不填實際帳號／地址。正常例：原始1920×1080/20FPS、預覽1920×1080/5FPS、AI640/3FPS；必須標「示例」，不是實測性能承諾。

推薦白話：

- 「已收到畫面」≠「服務正在執行」；「通知已開啟」≠「測試已送達」。
- 「身分尚未確認」取代對普通使用者直接unknown；技術碼在展開區。
- 「已保存，重新啟動後套用」；「正在接入，等待第一張影格」。
- 「將停止這台電腦的監控與通知，網頁服務也會關閉；資料保留」。
- 「原始截圖失敗，已使用預覽截圖（實際尺寸）」；不固定說1080p。
- 「模型分數0.82（尚未校正）」不寫82%肯定是貓。

## 10. 技術交接與實作限制

Jinja/FastAPI、原生JS/CSS。設計輸出不是把後端換成React或第三方雲端；任何框架遷移另立任務。保留DOM ID/事件、auth/CSRF、共用影格、保存語意與後端權限；不能靠前端隱藏作為安全驗證。

關鍵契約：status、preview、source/config、settings、pets、events、analytics/resources、analytics/behavior、analytics/cat-feedback、telegram/config、tailscale/status、network/dashboard、xiaomi/setup與devices/{opaque_id}/profile/ptz/privacy。均透過同源Dashboard API；前端不直連另一個go2rtc localhost URL。`/healthz`是有意提供的安全健康端點，不能在盤點中錯誤宣稱所有端點均需登入。

接口細節以 `src/meowkeeper/app/web/api.py` 與JS為準，Figma不收Token／Cookie／真實DB。角色目前是本機管理者Session與Telegram各自權限，不捏造完整家庭多角色管理UI。

## 11. 設計驗收清單

- 所有目前頁面／欄位／操作有映射，新功能清楚標proposal。
- 正常、空、錯誤、過期、停用、能力不支援有frames，不只happy path。
- 3秒看懂監控／畫面／通知，來源切換、保存、重啟影響明確。
- 手機／鍵盤可操作；PTZ不被表單方向鍵誤觸，停止可立即找到。
- 密碼／配對碼／Chat ID遮罩；沒有真實私人影格、秘密或帳號。
- 低負載、本機資源，不增加來源連線／持續動畫／外部追蹤。
- 図表單位、取樣／缺口／資料涵蓋時間清楚；可讀表格不能移除。
- 不把UI原型或設計獎項目標當成實際裝置／Telegram／模型準確率通過。

## 12. 參考資料與可提供資產

本地實作證據（路徑相對repository root）：

- `src/meowkeeper/app/web/templates/`：頁面與固定欄位；`static/js/`：動態元件／操作。
- `src/meowkeeper/app/web/static/css/experience.css`、`static/style.css`：目前視覺與responsive。
- `src/meowkeeper/app/web/api.py`：route／權限與接線。
- `assets/tray/meowkeeper-earth-ready.ico`、waiting/error：現有Windows圖示；[品牌SVG](figma/brand-mark.svg)供Figma參考，不是新增商標設計。
- [UI品質標準](UI_QUALITY_STANDARD.md)、[架構](ARCHITECTURE.md)、[安全](SECURITY.md)、[防重犯](LESSONS_LEARNED.md)。
- [r89通知／位置圖](R89_NOTIFICATION_POSITION_RELEASE.md)、[r90原生Tray](R90_TRAY_MENU_FIX.md)、[資源歷史](RESOURCE_HISTORY.md)、[Telegram回饋](TELEGRAM_CAT_FEEDBACK_PLAN.md)。

外部參考：

- [Figma官方Auto Layout](https://help.figma.com/hc/en-us/articles/360040451373-Guide-to-auto-layout-in-Figma)：可調內容的結構，不把所有元素absolute定位。
- [WCAG2.2 quick reference](https://www.w3.org/WAI/WCAG22/quickref/)：對比、focus、reflow、keyboard、label、狀態訊息。
- [Webby評審標準](https://www.webbyawards.com/judging-criteria/)：內容、結構、視覺與使用體驗的品質參考，非本專案已獲獎。
- [Awwwards](https://www.awwwards.com/)、[FWA](https://thefwa.com/)：視覺案例靈感；不可為追求展示動畫犧牲24H低負載。既有mobile PDF本輪未成功讀取，不引其未查證內容。

若要加入截圖，請在已部署版本截取全部主頁的去識別畫面，遮住家庭影像、配對碼、Chat ID、秘密及完整來源網址；標示版本／尺寸／狀態。未提供截圖時，Figma應把本文件與模板作為結構參考，而不是捏造目前像素級畫面。
