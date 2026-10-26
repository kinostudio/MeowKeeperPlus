# MeowKeeper+ 防重犯手冊：錯誤、證據與處理方法

## 共享播放／收音防退化

一般整頁導覽會銷毀 video 與媒體連線；只共用 go2rtc producer 不代表瀏覽器不重接。總覽／即時頁需同文件共享播放器，驗證同一 video、session 與實際前進影格。來源 `audio=0`、播放協商漏音軌及瀏覽器阻擋聲音是三種不同問題；取消靜音不能當成功。明確來源收音改動需短暫停管線，先備份、保留單一 producer、失敗恢復；不直接 DELETE 串流冒充已停止舊連線。回歸入口：`MONITOR-NAVIGATION-SESSION`、`SOURCE-AUDIO-SAFE-OPT-IN`，詳見 [契約](MONITOR_PLAYBACK_SESSION.md)。

整理日期：2026-10-12。涵蓋目前可取得的對話、repository 文件與驗收紀錄；未取得的原始資料不得補造。本文件不是功能完成清單，也不保證所有歷史問題已修復。

## 如何使用與判斷證據

- 開始診斷前只讀相關案例，對照本輪版本、程序、設定及日誌，不必載入整份手冊。
- **歷史驗證**：當時的原始報告或測試支持；換電腦、版本、帳號、路徑後須重新確認。
- **使用者回報**：對話提供的實測輸出，應保留其環境限制；不是代理本輪重新實測。
- **待驗證／建議**：不能描述成已實裝或已成功。
- 「已寫程式」「已測試」「已打包」「已安裝」「正在執行」「真實端到端通過」是不同狀態。
- 舊文件中的 PID、IP、版本、成功狀態或操作授權不是現況。現行規則以 AGENTS.md 與本次使用者要求為準。
- 報告只保存安全摘要；不得收錄 Token、Cookie、帳密、DID、MAC、完整私人來源 URL 或原始雲端回應。

## 文件索引

- 小米區域／MIoT：[Region SOP](XIAOMI_MIOT_REGION_SOP.md)、[SG／JPEG／升級歷史](XIAOMI_SG_JPEG_SYSTEM_UPGRADE_SOP.md)
- 防火牆：[UDP 相容性](XIAOMI_UDP_FIREWALL_COMPAT.md)
- PTZ：[位置讀回](PTZ_POSITION_READBACK_REPORT.md)、[實驗版](XIAOMI_MISS_PTZ_EXPERIMENT.md)
- 遠端：[Tailscale](TAILSCALE_INTEGRATION.md)、[C701 中繼配對](C701_REMOTE_RELAY_PAIRING_RELEASE_2026-10-06.md)
- 模型／回饋：[身分未知診斷](IDENTITY_UNKNOWN_DIAGNOSIS.md)、[增量學習](FEEDBACK_INCREMENTAL_LEARNING.md)、[Telegram 回饋](TELEGRAM_CAT_FEEDBACK_PLAN.md)
- 資源／发布：[資源歷史](RESOURCE_HISTORY.md)、[r88](R88_SYSTEM_STATUS_RELEASE.md)、[Windows 建置](BUILD_WINDOWS.md)
- 來源：[YouTube](MVP_PHASE3D_YOUTUBE_SOURCE_REPORT.md)、[架構](ARCHITECTURE.md)、[測試](TESTING.md)、[安全](SECURITY.md)

## 一、開始工作與完成宣稱

### 01｜摘要、README 與 Mock 不等於實作證據

曾出現文件聲稱功能存在，但使用者前端沒有按鈕、設定未接線或實機無回應。先追蹤 route → service → runtime → 持久化 → 實際回應；單元測試只能證明所測範圍。驗收必須區分 `passed / failed / not_checked`，不能以健康檢查替代 Telegram、PTZ 或真實影格驗收。

### 02｜工作目錄與 patch 寫入綁定不同

歷史曾回報 reparse path／寫入拒絕。PowerShell `Set-Location`、使用者給予完整權限，不保證 Codex task 的 writable roots 已更新。先核對 task 工作區及工具實際錯誤；不得繞過安全限制，也不得把失敗說成修改成功。本次能套用文件 patch 只證明本次寫入有效。

### 03｜模型與子代理設定不得假裝切換成功

主模型選單可選 Luna，不證明原生子代理支援。依 live schema、角色設定與實際 runtime 判斷；無法觀察模型／effort 時標 `UNVERIFIED`。受限委派需明確輸入、輸出、停止條件；主代理親自驗收，不擴大任務或讓 worker 改整體目標。

### 04｜過度重試與上下文膨脹

相同失敗條件不反覆重跑、不反覆要求登入、重啟攝影機或提供大型提示詞。先擷取遮罩後錯誤，只有新證據或修正改變條件才重試。長任務保存可恢復進度；登入等待是中間狀態，不是最終失敗。

## 二、Windows、啟動器與發布

### 05｜PowerShell 5.1 中文亂碼引發 ParserError

曾出現中文字串亂碼、引號／大括號錯誤。Windows PowerShell 5.1 對無 BOM UTF-8 可能按舊編碼解讀；發布腳本須以相容編碼保存並做 parser 檢查。不能把語法錯誤當攝影機或防火牆故障。

### 06｜StrictMode 下純量沒有 `.Count`

安裝器曾在 `Where-Object` 條件中因純量 `.Count` 失敗。集合需明確 `@(...)`，驗證零筆、一筆、多筆；不可僅在開發機多筆結果下測試。

### 07｜參數繫結失敗不是串流失敗

防火牆 Install 曾回報 `ParameterBindingArgumentTransformationException`。先核對型別、陣列與參數繫結的安全錯誤，再判斷規則是否存在；不能因腳本尚未執行規則便宣稱網路修復失敗。

### 08｜遠端路徑與腳本下載端點不能寫死

不同電腦的使用者名稱、桌面目錄與解壓根目錄不同；先確認實際檔案。下載失敗不執行半成品，更新須校驗 SHA-256、備份原腳本，再替換；舊 helper HTTP 服務可能已停止，不能沿用失效網址。

### 09｜程序數量必須核對完整程序樹

Host、Tray、go2rtc 使用跨程序鎖，啟動前依健康狀態、PID lock 與 port owner 檢查。核對 executable、command line、Parent PID、CreationDate、SessionId 及執行帳號；venv redirector 的父子程序不能只按名稱判為雙 Host。開發用絕對 `.venv` 路徑；發布用內附 runtime；禁止 PATH 自動選系統 Python。

### 10｜舊 Tray 與自動啟動來源

舊開發 Tray 曾占用同名鎖。核對排程器、Startup、Run／RunOnce、helper 與 mutex/PID 狀態；已健康 Host 只開 Dashboard。stale PID 須驗證身分才清除，不得按 python/pythonw 名稱批次終止。

### 11｜升級時 go2rtc／FFmpeg 仍鎖住舊 App

先停止可證明屬於 App 的 Host、watchdog、排程與子程序，避免殺掉後立即重生；刪除前確認鎖已釋放。仍占用則中止，保留 Data 與可回退版本。歷史「停止所有 ffmpeg.exe」會影響別的軟體，不作為通用流程；安全預設是精確路徑與程序關係。

補充（2026-10-16 r101 升級失敗）：安裝器已確認停止舊 Core PID 38780，但遞迴移除 App 時 `cv2.pyd` 回報 Access denied。輸出本身不能證明持鎖程序是誰；由於 AI 使用 `multiprocessing`，worker 的命令列不一定含 `run_host.py`，所以孤兒 worker 是合理可能但仍未實機確認。修正採舊 App 內附 Python／Pythonw 完整執行路徑（不按全域名稱），先處理子程序並逐一核對 PID／建立時間／即時命令列；舊 App 先同磁碟封存改名，通過新版 manifest 驗證後才清理。若封存因占用失敗，舊目錄不會被遞迴刪到一半；前次已留下缺 manifest 的目錄時，明確 `-Upgrade` 會封存恢復，不把它誤認成全新安裝。實機重試仍須回報程序退出、封存／校驗結果與實際安裝狀態。

補充（2026-10-16 r103 升級失敗）：程序清單列出 PID 後，程序可能在 `GetProcessById` 開 handle 前自行正常退出；這是快照競態，不是權限或 App 損毀。捕捉「PID 不存在」例外後，必須重新查詢該 PID：確認已不存在才略過並繼續最終程序清單驗證；PID 仍存在（可能被重用）或即時清單查詢失敗時，維持 fail-closed，中止且不移除舊 App。回歸測試須涵蓋已退出、PID 仍存在及程序資訊不可讀三種情況，不能只測程序路徑篩選。

### 12｜安裝看似卡住可能是 QuickEdit

歷史 r32 的管理員 PowerShell 標題顯示「選取」，CPU／I/O 停止、沒有 schtasks 子程序；Esc 解除選取後繼續。先檢查視窗狀態、程序樹與等待狀態，不直接歸咎排程器；Ctrl+C 可能取消真正安裝。

### 13｜UAC 沒出現或取消不等於執行成功

不可自行核准或繞過 UAC。現行升級一律提供精確包路徑與代碼，由使用者執行；不自動觸發安裝器。驗收另確認 installed manifest、程序 executable 與執行版本，不能只靠「Attempted to run task」。

### 14｜發布包被 runtime cache 汙染

歷史 `data/torch/hub/trusted_list` 不在 manifest，安裝正確拒絕。可變 cache／使用者 Data 與不可變發布檔分離；必要空白 metadata 在建置階段納入。不得放寬完整性驗證來掩蓋汙染，也不得修改已發布包後沿用舊 hash。

### 15｜SYSTEM 與使用者秘密儲存不同

Credential Manager、DPAPI、AppData 與 go2rtc Session 取決於執行帳號。使用者保存的秘密不保證 SYSTEM 能讀；須在對應 Host 的安全設定流程確認。可攜包搬移 Data 不等於可跨帳號解密所有秘密。

CPU 測溫缺值不可只叫使用者尋找 CMD：首頁須有可見啟用／診斷連結。首次載入與 GET 不啟動安裝；POST 須驗登入、CSRF、本機實際來源及明確確認，且核對固定工具 manifest。遠端或 session 0 不能假裝能顯示主機 UAC，不能自動安裝驅動或以流程結束宣稱已測到溫度。回歸 `CPU-TEMPERATURE-SETUP-ENTRY` 與 `tests/browser/cpu_temperature_setup.cjs`。

## 三、小米登入、Region 與雲端

### 16｜port parser 誤判導致 Session 遺失

對話回報 Phase 3C.4 誤判 `listen: :1984`，將 config 覆蓋為空。修正原則：先備份、正確解析、保留未知欄位與 xiaomi section；解析失敗 fail closed，絕不以空設定替換。變更 port 不得順便重建 Session。

### 17｜POST 登入與 GET 驗證必須分開

POST `/api/xiaomi` HTTP 200 空 body 表示請求接受，再 GET 驗證帳號存在／新 UID；帳號陣列不是 `unknown_error`。401 依 CAPTCHA、phone/email challenge 分類；500 或非預期錯誤為失敗。重啟同一 config 後仍有帳號才證明持久化。

### 18｜70016 不足以判定密碼錯誤

`captchaUrl=null` 不排除驗證、風控或 API 相容性限制。保留安全 LoginError、HTTP／Content-Type，分類 challenge、風控、unsupported、network、unknown；加入冷卻與使用者明確重試，不自動反覆送帳密。

### 19｜code -6 與 p2pid 字樣不能直接歸因

歷史 miss_get_vendor 回應 HTTP 200、code=-6、result absent；失敗在 vendor 解析前。公開錯誤碼定義未確認，不能直接說 ACCOUNT_REJECTION 或 CS2 dial 故障。暫定 `XIAOMI_MISS_GET_VENDOR_INVALID_REQUEST_CODE_MINUS_6`，以同次新 Session/device list 與獨立 client 安全比較；禁止保存原 response 或 token。

### 20｜後端 Region 不等於米家 App 地區

歷史 sg 成功不是「台灣＝sg」通則。Discovery 比對同台裝置、online 與真實影格後才保存裝置 region；candidate 不等於 verified。不要為診斷要求改 App 地區，也不能拿舊 cn DID／Session 驗證新的 sg 請求。

### 21｜PR 適用性先看真實 vendor 證據

PR #2264 的既定實驗門檻是 vendor=cs2、has_p2p_id=true、官方無影像且 LAN 路徑不可用；vendor=null／code=-6 不適用。PR #2272 需真實 prefix 條件。實驗使用獨立 source/binary、記錄 SHA，不偷偷替换正式 vendor。

### 22｜MIoT 臨時串流與 MISS 不同

歷史 MIoT action 成功回傳臨時地址，仍需實際取流；可能是 TLS RTSP，不可忽略協定或有效期限。OpenCV／FFmpeg TLS backend 差異可造成一個失敗另一個成功。地址只在記憶體使用，不存 config、DB 或日誌；action code=0 不等於取得影格。

## 四、防火牆、Tailscale 與真實影格

### 23｜先驗網路邊界，避免無效 profile sweep

檢查實際 go2rtc 路徑／PID／版本、camera route、介面狀態、Public/Private、UDP 規則及 Block。歷史 C701 同區網、Public、無適用允許規則，建立限縮 TemporaryRule 後才取得 JPEG；這是有對照測試支持的原因，不應先反覆重啟或換型號。

### 24｜Public Block 不能被新增 Allow 抵銷

另一筆使用者回報包含 Private Allow 與 Public UDP Block：目前 Public 下 Private Allow 不適用，明確 Block 可優先於 Allow。先核對完整 filters；只有另獲授權才處理精確 Block，備份與提供還原，不批次刪除使用者規則。

### 25｜防火牆規則路徑隨發布包改變

規則限制已驗證 go2rtc executable、攝影機 IP、實際需要的 UDP 與 profile。每次換路徑重新驗證規則；不得關閉整體防火牆、開所有程式或暴露 WAN。預設唯讀；正式 UAC 授權後冪等建立／移除，僅移除自己建立且識別可驗的規則。

### 26｜C701 成功證據的範圍

使用者回報 TemporaryRule：probe/frame HTTP 200、JPEG 38,959 bytes、848×480；後續 Install：installed_and_verified、JPEG 40,460 bytes、848×480。證明當時攝影機所在地電腦可解碼單張影格，不證明目前遠端 Host、PTZ、10 分鐘或 24H 成功。

補充（2026-10-19，r107 遠端實機）：攝影機可 ping、go2rtc 可完成 CS2 連線但 TCP／UDP 媒體讀取逾時，不能直接歸因防火牆。MIoT 同帳號同裝置讀回 `camera-control:on=false`、`property_code=0` 才確認原生待機。排程工作 principal=SYSTEM 不代表當前 Host 或 go2rtc 的 owner；本次實際兩者均由互動使用者執行。另 `/api/streams` 的 source URL 會遮罩 userinfo，不能將其原樣重新登記；來源更新須由同區域新裝置清單取得完整來源並比對裝置，僅留在記憶體。待機診斷不得自行開鏡頭，恢復拍攝須有明確使用者決定。回歸台帳：`XIAOMI-STANDBY-NO-FRAMES`；實機與隔離狀態見 [r107 C701 診斷](C701_INSTALLED_R107_RECOVERY_2026-10-19.md)。

### 27｜Tailscale 路由可達不等於 Xiaomi UDP 可用

攝影機在另一住處時，subnet routing／ping 成功不保證 MISS 本地 UDP 路徑可用。所在地需有已驗證 ingest 的中繼，Tailscale 本身不產生 RTSP。依實際網卡、route、配對與端點判斷本地／遠端，不能把 C701 型號寫死為遠端。

### 28｜遠端配對須保存控制與來源關係

配對後保存裝置與受限中繼的安全識別，不要求普通使用者猜 RTSP。畫質／PTZ 控制需送往真正持有 producer 的端點，不是永遠本機 API；先本地或使用明確模式，fallback 只能在已配對並授權的中繼範圍內。

### 29｜RTSP 區網公開需獨立授權

Dashboard LAN、go2rtc API、RTSP 是不同服務。歷史有 Private TCP 8554、LAN-only 開放授權，不代表 API 1984／WAN 開放。裸 RTSP 沒有 Dashboard 登入保護；需說明並限縮，不能因預覽需要把管理 API 一併公開。

### 30｜HTTP 200 空 body、producer=1 不等於影像

stream PUT/PATCH 成功只代表註冊。producer 欄位可能仍是未活動來源，consumer=0、無 bytes/protocol 不能證明有 Video Packet。JPEG 要檢查 bytes、解碼、尺寸及新鮮度；第一影格與持續串流分開驗收。

### 31｜JPEG API／FFprobe 失败可能是 decoder 問題

HLC7 歷史曾 API 500／FFprobe 失敗，但內附 FFmpeg 在同一 go2rtc stream 解出真實 JPEG。先用有限逾時 FFmpeg fallback，不新增攝影機連線；失敗不能直接說沒有串流或型號不支援。JPEG bytes=0，即使 HTTP 200 也不合格。

### 32｜subtype 不存在通用解析度表

sd、hd、auto、0–5 是協定選項，不保證每個 firmware 對應相同畫質。C701 subtype=3 的 4K 回報不能套到所有舊機；以本輪 codec／resolution／FPS 為準。切換替換同一 stream，等待新鮮影格，不並行 sweep。

## 五、Dashboard、預覽與 PTZ

### 33｜有預覽不代表設定與監控完整接線

歷史即時有畫面但 settings 仍 mock、接入按鈕說失敗。保存設定與 runtime 必須一致；非同步 `starting` 不是最終失敗。來源切換／重啟須恢復 Xiaomi 裝置識別、region、profile、控制端點，不只保存 RTSP 字串。

### 34｜區網 UI 不能用 localhost 絕對 URL

前端 API／影像／下載用同源相對 URL，WebSocket 用目前 host。共享 nav 保持設定／維護標籤。0.0.0.0 是 bind address，不是可分享網址；有效 LAN IPv4 與 Tailscale URL 分開驗證。登入保護涵蓋 API、預覽、影片、下載與 WS，不把日誌公開给未登入者。

### 35｜PTZ 必須綁活動中的相同 producer

使用者曾回報即時848×480正常，但 PTZ 503 `xiaomi_producer_not_active`；另有正式與中繼兩個 go2rtc。先對照 FFmpeg RTSP target、port owner、stream、producer 活動狀態及控制端點；不能只因串流註冊就判斷可控，也不能直接判馬達壞。

### 36｜ACK、移動與讀回是三個驗收

歷史 PTZ HTTP504／response_timeout 不是成功；「操作已送出」不等於馬達動或位置可讀。優先短促上下、finally stop、限制時長；停止沒確認需提示。位置讀回需真實裝置回應，不以推估座標或 UI 文案充當成功；未活動也不是不支援的證據。

### 37｜隱私位置與原生待機不同

啟用前保存可讀原位置，恢復後讀回確認；有畫面不代表回到原角度。MIoT 原生開關需確認 off/on 回讀，會中斷影片，不等於切斷電源或實體遮罩。裝置規則與失敗狀態需分開，不把其他型號成功套到 C701。

### 38｜原始影像、預覽與 AI 張量不同

4K 影片、1080p 截圖／預覽、640 AI 可以同時存在。縮放解碼後影格成 AI 張量不是再編碼一條影片；負載仍取決於解碼、縮放與推論。截圖優先原始來源分段，降級時明確顯示實際預覽尺寸；不能用預覽尺寸聲稱來源最高畫質。

### 39｜Tray 選單不可阻塞 UI thread

狀態、停止與網路操作應由有限背景工作處理，防重複點擊並通知成功／失敗／進行中。完整關閉只停止本應用擁有的核心、Tray、Dashboard 與子程序；無登入仍監控由核心服務模式承擔，不依賴開著瀏覽器。

r95 補充：不同 port 不會隔離 Cookie。正式 Dashboard 與測試 Dashboard 共用 127.0.0.1 時，測試登入會覆蓋正式 session；隔離 UI 驗收改用 localhost，正式保留 127.0.0.1。首次設定不一定是帳號遺失：先核對 port owner／可攜或安裝 root／實際 admin storage。原 r94 可攜 Host 占用 8000，但 ProgramData/Data 管理者資料仍存在，停止該已識別來源並啟動正式 Core 才恢復原登入，不能重建帳號蓋掉既有資料。使用者最新契約：停止監控停止整條影音管線，保留 Dashboard／Tray；不得混用只停 AI 的暫停語意。

r94 補充：不要為了「foreground=owner」把 pystray popup owner 換成 icon HWND。其 dispatcher 吞掉未處理訊息，原生選單應保留專用 menu HWND／DefWindowProc，並對同一 owner 設 foreground 與 WM_NULL。單元测试不能代替 Windows 右鍵選單實際可見文字驗收。

r94 首頁補充：共用 JS 必須版本化；圖表初始化不得依賴攝影機已出現影格。用 Host checked_at 比較來源時間，不能用手機時鐘當唯一新鮮度基準。AI 完成舊影格不得倒退最新 ingest 時間。IsolatedTracker 的 device 參數須以真實 signature 測試，避免 Mock 隱藏 TypeError；renderQuality(null) 不能因 undefined==undefined 而存取 null.quality。

## 六、Telegram、貓咪與回饋

### 40｜Bot 有憑證但 disabled 的落差

Chat ID 是 numeric ID，不是 @username。使用者先 /start，Bot 可回覆 ID，但不得因此授予管理權。設定 enabled、已選收件者、secret owner 與執行 sender 狀態須一致；依目前 lifecycle 支援確認是否需重啟，不能永遠顯示舊環境變數提示。

### 41｜Telegram ok=true 不等於手錶播放成功

送影片提供 width、height、duration；檢查 H.264、yuv420p、音訊相容性及 MP4 moov/faststart。相容影片直接封裝／上傳；索引問題優先 remux，HEVC／不相容像素格式才必要轉碼為 H.264/AAC，保留原片。message_id 不是播放證據。2026-10-14 使用者確認已在實機 Apple Watch 上接收並播放喵喵守護者+ Telegram 事件影片；面向此使用者的教學應明確寫可播放，不再加上「可能無法播放」的泛化保留語。此實測不推論所有 Watch／Telegram 組合。

### 42｜逐貓框號不是永久身分

Track 1／2 是當次畫面對照，遮擋或重接可變。使用貓框與编号確認人類誤框、數量及對應，再回報個體／行為。第一輪可依登錄寵物顯示「是某隻／兩隻都在／不是貓」，兩隻分別回報行為；姓名不寫死，回饋 opt-in，callback 驗證 event/chat/權限。

Telegram 多步回報的「同一 update 重送冪等」不能防止使用者雙擊產生兩個不同 update。新 staged 選單使用資料庫 revision token；舊版本只刷新、不 toggle 或提交。並以兩個 SQLite 連線競爭驗證交易序列化；這不等於實機 Bot 收件端已驗收。詳見 `V4_TELEGRAM_SEMANTIC_AND_MULTICHOICE.md`。

### 43｜cat_detected 與逐軌跡彙整不同觸發

目前程式檢查顯示貓咪事件影片與 V0.2 finalized 軌跡／行為回饋是不同通知分支，不能以收到一種推定另一種完成。分析層分開有理由，通知層應避免重複、保留同事件／軌跡關聯；合併通知是設計建議，不因寫入本文件而已實装。

### 44｜照片已存不等於已建立特徵

`DINOv2Unavailable` 時照片仍可保存，但 embeddings=0 無法當作已識別。檢查內附模型、source 與依賴，前端需編輯／新增照片／重試建立。裁切最低尺寸與品質應在收集時提示；太小、模糊、背景多或多貓混合不盲目加入。

### 45｜回報多不保證辨識準確

參考照片／embeddings 在 pet Data；Telegram 回饋與不可覆寫歷史在 SQLite；通知開關 JSON 不含學習標籤。增量參考特徵不是自動重訓 YOLO；信心分數不是準確率。重複低品質樣本不一定有益，沒有固定幾筆必成功；個體與行為仍需家庭資料驗收，未知先存 unknown。

### 46｜動作與行為維度不可混加

行走／奔跑屬動作，玩耍／進食屬主要行為；跑動不必然玩耍。兩隻貓各有 track／identity／behavior；不以整張影片一個標籤覆蓋全部。身分慢或失敗不能阻塞事件保存通知；聲音模組存在也不證明已接音訊 AI。

## 七、快取、資料與硬體

### 47｜環形快取不是長期 BGR deque

主要錄影保存壓縮分段，容量／秒數有限，事件引用期間 pin，完成後釋放。先 copy 組裝、關鍵幀允許小幅時間差；必要才轉碼，驗證實際時長。/live 最近片段與從現在錄影不同；Telegram、SQLite、AI 慢不得停止 ingest。

### 48｜YouTube 解析 URL 不可持久化

固定 yt-dlp／ejs／JS runtime、內附 FFmpeg；只保存原 YouTube URL。EOF、403、manifest 過期重新解析，有限退避、單一 producer；不靠 Cookie／OAuth 繞過私人、年齡／DRM 限制。mock、本地影片通過不代表公開 YouTube 當時可用。

### 49｜可攜包 Data 搬移不是完美跨機繼承

先完整備份，SQLite 使用一致性 backup，不在寫入時只複製主檔而遺漏 WAL。參考照片、特徵、回饋、設定與必要媒體需清楚清單；不備份 runtime cache。DPAPI／Credential Manager 秘密與外部 Session 可能需重新授權。加密匯出驗證 checksum、路徑穿越與容量限制；六字元最低長度不是強密碼保證。

### 50｜安裝套件不等於真正 GPU 推論

PyTorch CPU wheel、CUDA provider 名稱或 GPU 裝置存在均不夠；檢查實際 backend、warmup 與 kernel/provider 證據。MX250、RTX 開發機、Intel GPU 需分別驗收，不自動改驅動／CUDA。明確 Intel/ORT GPU 不可用時走可解釋 CPU fallback，不能把無效 device 字串傳給其他 backend。

### 51｜r88 CUDA DLL 與 runtime 版本教訓

前輪內附 runtime 缺 `cufft64_11.dll`，pip check 不能发现 DLL 載入问题；補齊專案 CUDA/cuDNN runtime 後 warmup 才成功，實際 CUDA kernel nodes=308、CPU nodes=55（shape/control 節點可留 CPU）。開發 venv 與內附 Python／OpenVINO 版本不同，不能把 venv 的 Intel 結果套到發布包。

### 52｜1080p／5 FPS 是目標，不是所有硬體保證

歷史短測 RTX 2060 約5.01 FPS、GPU平均29.04%；Intel 約4.68 FPS，均非 MX250 長期證明。1080p 張量可能按 stride 補至1920×1088。40% 是可配置软負載目標，不保證整台電腦永不超過；需含推論、影音與其他程式的長測。獨立 AI 程序採 bounded queue/latest-frame-wins，溫度感測缺失記 unavailable，不填0當正常。

### 53｜系統與應用用量不能混為一談

整機 CPU/RAM、應用程序樹 RSS、GPU裝置用量及磁碟容量／I/O 不同。近一小時平均／峰值應来自原始採樣，標示覆蓋時間、缺失與過期；I/O 累計計數需處理重啟／歸零，磁碟空間不等於讀寫量。Intel GPU 無可靠 sensor 不冒充 NVIDIA 指標。

### 54｜SQLite migration 與 Windows handle

資源歷史 v1→v2 新欄位使用顯式 column insert，避免依欄位順序。Python SQLite context manager 主要 commit/rollback，不自動 close；Windows 未關 handle 可導致 cleanup PermissionError。測試與正式 shutdown 都需確實 close，使用 WAL/busy timeout／有限重試。

### 55｜Python／embedded import 驗證環境要對

Python 3.10 沒有標準 tomllib，需適用 parser，不能因此誤報 TOML 壞掉。embedded `_pth` 可忽略 PYTHONPATH；要驗證真正入口與 sys.path，而不是只在 venv import 成功。編譯／import／pip check 都不能取代 runtime smoke。

### 56｜完整關閉後也必須有 GUI 重新啟動入口

Tray 的「完整關閉」會結束已安裝 Core／Tray 工作；如果安裝版只有系統匣入口，使用者把 Tray 關閉後就沒有可點的重新啟動方式，要求使用者輸入 CLI 不符合一般產品使用。安裝與升級須建立開始功能表捷徑；捷徑驗證排程 Action 或目前使用者 Startup 命令確實指向固定安裝，再啟動 Core、等待健康檢查、啟動 Tray 並開 Dashboard。解除安裝只能移除確認屬於本產品的捷徑。以回歸測試納入發布門檻；不以「Tray 下次登入會自動回來」代替立即重新啟動能力。

### 57｜P95 反轉不代表較大解析度更快

AI 尺寸比較需固定模型／影格／後端，正反交錯多輪，合併逐張原始樣本計算 P50/P90/P95/P99/max；不可平均每輪 P95。即時上限測試要在單張超時後重設排程，不允許追趕式連跑，並分開報告平均、中位數、尾端延遲與實際 FPS。曾在 RTX 2060 隔離矩陣觀察1920 px總 P95 低於960 px，但1920平均／P50較高，960／1280有CUDA呼叫長尾；整卡兩秒GPU採樣不足以和單張慢樣本對時，原因未定，不能據此修改正式解析度或宣稱高解析更有效率。數據需另驗目標 MX250 及完整來源／事件管線。

## 最近驗收基線與仍需確認

- 前輪 r88：529 unittest 通過（`reports/r88-unittest-final.txt`）；Ruff、compileall、兩個 runtime 的 pip check 通過；發布包完整性驗證通過，20,551 檔案。包位於 `dist/MeowKeeperPlus-portable-2026-10-12-r88`，**前輪未由代理安裝或重啟正式 Host**。
- 上述是最近已有工具證據，不是本次文件任務重跑全部測試，也不是「全部功能已完成」。本輪文件修改不會自動出現在既有 r88 包，須納入下次建置。
- 尚不能由本手冊宣稱：MX250／內附 Intel runtime 長期1080p效能、所有攝影機 PTZ／讀回、重新開機後持續影像、兩小時／24H 穩定性、目前版本真實 Telegram 逐軌跡點擊落庫，或目前 C701 遠端持續成功。各項須查原始驗收紀錄並補本輪證據。
- 正式 Telegram 端到端要求：Code → Package → Installed → Running → 真實訊息／按鈕操作 → SQLite 對應紀錄。舊 message_id、單元測試或 outgoing /status 均不可替代。

## 診斷前與交付前最小檢查

1. 核對本轮 package、installed/running版本、Data root、帳號、端點與程序關係。
2. 將失敗定位在登入／雲端／網路／producer／解碼／控制／持久化哪一層，不從錯誤關鍵字跨層推論。
3. 保存遮罩 HTTP／錯誤碼／新鮮影格／實際 backend 證據，設定逾時與停止條件。
4. 只有修正改變條件才重試；維持單一攝影機 producer，不新增隱藏 consumer。
5. 完成宣稱列出實測範圍、未測項、rollback；不把本文件的建議當程式已修復。

## 新案例記錄格式

2026-10-17後，會重犯的已解決故障還需登記 `configs/release-regressions.json` 並接入建置門檻，流程見 [工程回饋閉環](ENGINEERING_FEEDBACK_LOOP.md)。文件記憶不是程式防護；門檻成功也不是所有電腦永不故障。程式或台帳更新後不得沿用舊綠燈，真實外部驗收仍需對應版本證據。

```text
日期／版本／環境：
症狀與影響：
安全原始證據與報告路徑：
已確認原因／尚未排除的原因：
最小修正與修改檔案：
修正前後驗收（passed/failed/not_checked）：
停止條件／回退方法：
下次必須先檢查：
```

新案例附原始證據引用，不覆蓋歷史失敗。更改診斷結論時指出新證據；文件紀錄能降低重犯，但不能代替程式保護與真實驗收。

## 2026-10-16｜r99 預覽與區網回報實機回歸

- 首頁狀態有新影格而預覽空白，先查console。此次原因是shared-preview的未宣告statsTimer／probe，無須重新登入、掃profile或調整小米雲端。
- node --check只驗語法，不會發現事件執行時的未宣告變數。發布前須實際mount並走成功JPEG回呼、模式切換、隱藏／恢復及清理，搭配真實瀏覽器。
- HTTP區網不是localhost安全內容；crypto.randomUUID可能不存在。回報識別碼用getRandomValues相容UUIDv4，不使用Math.random，也不要求使用者為此關閉登入保護。
- MSE協商成功、影片1920×1080、source=20 FPS與瀏覽器呈現20 FPS是不同證據。此次舊HUD按回呼次數誤報約1 FPS；須用presentedFrames差值並另查解碼／掉幀計數，不由來源FPS推論。SourceBuffer.remove也會觸發updateend，清理前必須確認緩衝仍有可刪範圍，否則可形成循環阻塞新片段；須測超過25秒的實際播放。
- 建置需核對既有功能選項：r99未帶enable-verified-ptz而回到官方backend。需要PTZ的候選包須明確帶既有驗證backend與hash，保留官方binary，不能以已打包當作實體移動成功。
- 詳見[R100_BROWSER_ACCEPTANCE_FIX.md](R100_BROWSER_ACCEPTANCE_FIX.md)。

## 2026-10-16｜預覽尺寸、採樣日誌與低溫降階

- V0.2 正常略過採樣不是分析失敗，也不是「沒有貓」；不得統一印警告、清空有效框或算成完成 AI 分析。固定安全分類並彙整次數。
- GPU 低溫／低即時負載不能推翻先前端到端耗時降階。保留觸發當時指標與每張預算，平均多次真正分析，排除 wrapper 自己的感測輪詢；重試應明確套用使用者已保存上限，不永遠卡在低頻。
- 1080p JPEG 的設定不限制 MSE 原碼解碼。請用 videoWidth/videoHeight 查實際影片尺寸，勿以 CSS 尺寸或 JPEG 規格冒充原碼規格。
- Windows 快取讀者需允許 SHARE_DELETE；遺留暫存檔清理失敗不能阻止新影格。WinError 5 仍可能是外部占用或權限，不可只靠代碼定因。
- 缺完整歷史逐幀座標時，不能把單張框套滿整部舊影片；選用匯出須重分析並交代取樣／有效時間，不偽造個體或動作。
- 新修正與舊候選包必須分開：詳見 [PREVIEW_AI_DIAGNOSTICS_2026-10-16.md](PREVIEW_AI_DIAGNOSTICS_2026-10-16.md)。

## 2026-10-16｜r104 流暢模式握手 404 與節能留白

- 正式 HTTP 頁面正常、MSE 一切換就失敗時，先查 WebSocket Upgrade 的 HTTP 狀態。此案未登入握手回 404（應由 WS 路由拒絕為 403），內附 Python 確認缺少 websockets；Uvicorn 自動降為沒有 WebSocket 的 HTTP 服務。不是 HEVC 解碼、攝影機畫質或登入過期造成。
- pyproject 已宣告依賴不代表 prepared runtime 已安裝。發布檢查必須要求 websockets，並以內附 Python 實際匯入 client 與 Uvicorn server transport；固定 15.0.1 供 proxy=None 契約。Host 明確選 WebSocket transport，缺件時用中文停止啟動，不默默提供不完整功能。
- .venv／TestClient 成功不能替代內附 Python 的真實網路握手與瀏覽器解碼。此次使用實際 app factory、實際已登入 WS 路由、內附 Python 和既有正式 go2rtc 來源完成隔離切換；未建立第二條攝影機 ingest。
- 檔案雜湊通過只證明包內檔案符合 manifest，不能證明 manifest 沒漏必要依賴。
- 16:9 容器滿寬時，媒體再套 max-height:70vh 會在大視窗縮成較小畫面、留下側邊與下方空白。JPEG 與 MSE 都使用容器的完整寬高和 object-fit:contain，尺寸限制應加在容器而非另縮媒體。

## 2026-10-17｜CPU 溫度不是主機板溫度或歷史快照

- 已安裝驅動不代表目前程序有存取權限；一次管理員測溫成功不代表一般權限 Host 可以持續讀值。顯示 permission_required，不自行提權、修改 ACL 或拿先前數字充當即時。
- CPU 感測背景工作不可為每個 API／觀看者重啟；也不可為判斷監控開關而每 5 秒收集完整程序／GPU metrics。使用輕量生命週期 callback 與共用快取。
- 過期溫度必須清空數值並保存 NULL，不填 0；主機板、ACPI、AMD Tctl 不冒充 CPU Package／Tdie。
- 可執行回歸：`tests.test_cpu_temperature_monitor`；台帳 `CPU-TEMPERATURE-CACHE` 強制包含共用、過期、登入保護及輕量 callback 測試。接線與未驗範圍見 [持續觀測報告](CPU_TEMPERATURE_CONTINUOUS_INTEGRATION_2026-10-17.md)。

## 2026-10-18｜接入、播放及控制需各自證據

- 按需 H.264 不可為方便而關閉 go2rtc 的 insecure producer 保護。本次 API 動態 exec 被拒絕，改 Host 持有 FFmpeg、只提供本機隨機端點、註冊普通 HTTP 媒體。內附 RTSP demuxer 不接受 `-rw_timeout`，須看 stderr 後用 `-timeout`，不能盲重試。
- 共用轉碼要測原碼優先、零觀眾零工作、雙觀眾同 producer、晚加入 init fragment、最後離開及 session 清理。台帳 `PREVIEW-ON-DEMAND-H264` 連到 `tests.test_preview_transcode.TranscodeTests`；真實合成媒體／Edge指令與限制見[報告](ON_DEMAND_H264_PREVIEW_2026-10-18.md)。

- 註冊 producer、HTTP 200、指令送出不是有效影格或實際到位證據。接入工作要影格、絕對定位要讀回；停止未確認必須保留失敗狀態。
- 中繼只允許固定串流、TCP 與授權對端；不按型號猜遠端，也不為預覽開管理 API。回歸：`tests.test_camera_connection_workflow`，台帳 `CAMERA-ONE-CLICK-RELAY`。
- 真實 loopback 媒體測試要先確認容器；本次 exec 裸 H.264 出現 unsupported header，MPEG-TS 可用。合成媒體通過不可當作異地攝影機證據。
- aiortc worker 停止與 track.stop 可能重複關閉 container；須先脫離回呼再單次關閉。強制回歸 `WEBRTC-SHARED-SESSION` 保留真實雙 peer 釋放測試。
- 視覺不只模板斷言：第一屏幾何、手機無溢出、全螢幕提示、觸控／鍵盤與無 pageerror 均需瀏覽器操作。[本輪報告](CAMERA_MONITOR_REDESIGN_2026-10-18.md) 分開隔離與正式待驗。

## 2026-10-18｜首頁服務卡片與攝影機預覽不可為了首屏壓縮

- `.hero-services` 若隱藏 `.status-rows`、`.field-help`、`.card-footnote` 或英文副標，會讓攝影機、AI、Telegram 三張卡只剩標題和狀態；不可用隱藏資訊來塞進首屏。
- 首頁攝影機預覽須保持 16:9 與 `object-fit: contain`；對預覽容器套 `max-height:60dvh` 會使矮視窗中的畫面比例錯誤。桌機首頁預覽／硬體區為 60／40，窄版改堆疊，服務卡資訊保持顯示。
- 回歸門檻 `DASHBOARD-SERVICE-CARD-DETAILS-AND-PREVIEW-SIZE` 檢查卡片欄位及尺寸契約；瀏覽器量測亦需覆蓋 390、768、1000、1440 CSS px，確認 16:9 且無水平溢出。

## 2026-10-18｜位置讀回或 API 功能旗標不能證明絕對 PTZ 可用

- go2rtc 暴露 `position`／`SetPosition` 或回傳 `absolute_move=true`，只代表程式存在這條命令路徑；必須以該型號真實目標、命令後的新鮮位置讀回驗證，才能解鎖座標圖與位置隱私。讀回成功與絕對移動成功是兩個獨立能力。
- 方向 `move(direction)`、`stop` 與絕對 `position(angle,elevation)` 是不同協定操作；前者成功不可推論後者成功。命令 API 回應只可標「已送出／已接受」，位置變化需讀回證明。
- 沒有絕對定位證據時，不讓座標圖看起來可操作，也不保存無法可靠恢復的隱私位置。將來若改用相對方向閉環，必須先校正四方向與讀回軸的映射，逐步短脈衝、每步停止與讀回，並設最大步數／逾時／無進展中止。
- 修復報告：[r107 Xiaomi 隱私與 PTZ 修復覆核](R107_XIAOMI_PRIVACY_PTZ_FIX.md)；發布回歸門檻 `XIAOMI-PTZ-PRIVACY-READBACK-GATE`。

## 2026-10-19｜安裝資料根目錄、事件影片與防火牆診斷不可混為一談

- 安裝器排程明確傳入 `ProgramData\MeowKeeperPlus\Data`，但舊／直接 Host 啟動可能仍明確帶有 `App\data` 參數；不能因新程式預設解析到 Data 就假定舊任務也已換路徑。Tray 開事件資料夾／匯出診斷時，必須依已登入 Host 的 `/api/v1/status.metrics.disk.path` 核對實際 `events` 根目錄，且只接受本安裝的 `Data` 或 `App\data`；不能自行依目錄是否存在猜測。安裝圖形入口依 Core 排程工作實際的 `--event-directory`／`--dashboard-config` 選擇等待的 Dashboard 設定，禁止硬寫 `Data`。升級仍先封存 App，再把舊 `App\data` 與根目錄 `config.json` 安全移入 Data；cache／locks／install-history 不搬，同名衝突 fail-closed、不覆寫，預驗證失敗依交易紀錄回復。開始功能表捷徑提供非 CLI 圖形重啟入口。回歸由 `INSTALLED-DATA-ROOT-CONSISTENCY` 強制。
- SQLite 事件若保存的是舊電腦絕對 clip_path，資料庫與目前資料夾可能同時有影片，但 UI 仍判定不可播放。只允許在設定事件根目錄依安全事件 ID 回退到 `event_<id>.mp4`；不可照 DB 任意外部路徑送檔。見 `EVENT-VIDEO-RELOCATED-DATA`。
- 2026-10-19 SHRIMP-ASUS 實機證明舊 r98 路徑的檔案甚至仍存在，但 r107 依目前事件根目錄限制回報不可播放。可用 `tools/repair_event_media_paths.py` 在 SHA-256 比對、SQLite 線上備份後條件更新路徑；不得關閉檔案路徑限制來繞過。745 筆修復、最新真實事件人工回報影片完整播放；另 95 筆缺少對應 MP4 仍如實保留。詳見 `C701_EVENT_DATA_REPAIR_2026-10-19.md`；回歸另含備份／冪等及錯誤雜湊不改寫。
- `/api/v1/status` 的 `dashboard.firewall` 是 Dashboard TCP 入站規則狀態，不是 Xiaomi go2rtc UDP 影格診斷。來源 running／連線數與 HTTP 200 均不能證明收到影格；必須分開檢查 frame timestamp、JPEG/影片解碼與防火牆診斷。小米頁「連線遇到問題？」中的唯讀按鈕需把完整安全結果呈現在頁面，不能只把摘要塞進提示列後丟棄回應；回歸 `XIAOMI-FIREWALL-REPORT-VISIBLE`。
- 實機狀態 JSON 中 `last_frame_at/resolution/fps=null`、preview 無新影格及 ring buffer 0，僅證明該快照未有可用影格，不能單憑此判定就是防火牆。實機 Host／當前封包路徑仍需另外驗證。

## 2026-10-17｜場景參考 UI 不可回退為百分比表單

- 行為／場景參考需讓一般使用者保存即時截圖或上傳參考照片，再以拖曳框選物件位置；內部座標可以是正規化數值，但不可要求使用者手填百分比。
- 導覽頁面中存在一支未掛載的前端模組，不代表功能仍在 UI。需測試真實 template 包含即時截圖、上傳、拖曳標記、標記保存與本機拼貼預覽，並確認舊百分比欄位沒有重新出現在可見表單。
- 外送場景參考必須與一般事件圖片外送分開同意，預設關閉並綁定 Provider／endpoint／model。只傳使用者選擇的物件裁切與標籤，不傳來源原圖；排除／隱私區永不外送。這些參考只作物件情境，不可當作事件行為或寵物身分證據。
- 可執行回歸已列入 `configs/release-regressions.json` 的 `SCENE-REFERENCE-UI-CONSENT`；此門檻要求新的拖曳編輯器存在、舊百分比表單不回歸，同時覆蓋 API 登入／CSRF、設定遷移與場景裁切。本輪完整測試先抓到舊表單斷言未更新，調整為新 UI 契約後完整843項通過；正式發布仍須重新執行本版門檻。

## 2026-10-20｜候選完整管線測試必須先驗證快照依賴

- CUDA 單張靜態影格成功，只證明該模型／尺寸／Execution Provider 可推論；它不覆蓋即時來源、V0.2 BoT-SORT 追蹤及事件完整管線。完整管線快照若漏掉 `configs/botsort_reid.yaml`，首次追蹤會在模型工作階段前失敗，不能拿零完成推論的執行結果判定硬體效能。
- 候選測試必須在連接即時 RTSP 前檢查此設定檔；回歸由 `CANDIDATE-PIPELINE-CONFIG-PREFLIGHT` 強制缺檔拒絕與完整快照通過。候選測試仍需至少三筆完成推論、有效實際 FPS；CUDA 還需真實 CUDA provider node 證據。後端標籤、來源 connected 或程序存活本身都不算通過。
- 收到來源影格不等於 AI worker 已完成初始化。舊候選計時器在首幀後就開始倒數，而同步分析可能正阻塞在 OpenVINO worker 啟動／warmup；此時 `get_metrics()` 的快照可能仍是舊的預設 CPU／0 FPS，導致把「沒等到推論」誤報成「Intel GPU 沒啟用」。現在必須等首筆完成推論後才開始效能窗，並同時取 live analyzer／worker readiness 與安全的初始化階段／例外類別；原始錯誤訊息和私人路徑不得寫進回報。`CANDIDATE-AI-FIRST-INFERENCE-DIAGNOSTICS` 對此設回歸。該修正只修量測與診斷，不證明 Intel 硬體可用或效能；實機重跑仍須在正式監控安全暫停後進行。
- 2026-10-20 修正後，才完成同候選／同來源／同 960 px 上限的 CUDA 與 Intel 實機全管線比較。兩邊均能完成推論但都未達 5 FPS 上限；排程等待及 IPC/RPC 差值很小，耗時主要落在模型執行及追蹤，故不能把問題歸因為「GPU 利用率不足」或溫度降階。CUDA 的 `nvidia-smi` 是整張 NVIDIA GPU 遙測；Intel 的這份 NVIDIA 專用遙測為 unavailable，不可記成 0，也不能據此判斷 Intel GPU 沒有運算。OpenVINO 首次初始化時間與穩態 60 秒測量窗必須分開解讀。詳細主機數據僅存於 Git 忽略的 `data/diagnostics/candidate-pipeline-20261020/`；不得推送公開 repository。
- 這是單一來源、單一設定的短時候選比較，不能代表長期穩定、其他來源、事件影片或 Telegram 延遲；未做一因子效能改動，也未升級正式 Host。正式監控恢復後只做 SSH 唯讀狀態確認；沒有候選程序殘留。要比較優化前後，必須再建立新候選、固定其餘條件並重跑相同測量窗，不能拿這一組數據宣稱已完成最佳化。
- Windows 本機重跑測試時明確使用 `.venv\Scripts\python.exe`；裸 `python` 可能解析到不含 `cv2`／Ruff 的系統 Python，造成環境匯入錯誤。這不是程式測試失敗；先確認實際執行檔與依賴環境，再解讀測試結果。

## 2026-10-20｜即時偵測框不可被下一筆空採樣立即抹除

- AI 完成一筆無偵測的採樣時，不能立刻覆蓋尚在有效時間內的最近正向疊圖；Dashboard 約每秒輪詢，而 AI 樣本時序可能讓正向框剛好落在兩次輪詢之間，造成看似從未顯示或閃爍。只可在相同來源 epoch 內短暫沿用最近正向結果，超過前端 2 秒有效窗、監控工作階段變更或時間戳失效即清除；不能用先前框冒充新一幀偵測。
- 使用者提供的事件影片可驗證實際 YOLO 正規化框如何映射到預覽尺寸，但模型漏檢仍需獨立如實標示。這種素材／JS 幾何測試不等同正式 Host 即時瀏覽器或所有貓咪的偵測驗收。
- 發布回歸門檻 `LIVE-AI-OVERLAY-SAMPLE-LIFETIME` 覆蓋正向結果短效保留、空結果逾時、來源 epoch 隔離及 3840×2160 事件影格的框位換算。

## 2026-10-20｜設定 API 通過不代表頁面已掛載保存功能

### 硬體 Hero 不能遺漏既有倒數元件

- `monitor-ui.js` 動態建立的硬體 Hero 只顯示採樣文字，原環形倒數仍在舊 `.dashboard-system`；只檢查模板含倒數 ID 會漏掉這種可見介面退化。改為搬移唯一既有倒數元件到 Hero 右上角，不另造採樣計時或硬體請求。回歸 `DASHBOARD-LIVE-HARDWARE-MOTION` 檢查搬移接線，瀏覽器 `tests/browser/dashboard_live_metrics.cjs` 必須同時載入 Hero builder 與動畫模組，驗證圓環變化、過期與窄版布局。

- 新版設定 partial 已被頁面引用，但唯一的前端 script 標籤仍在停用的舊版條件區塊內，會讓整個管理中心永遠停在「讀取中」，保存亦無事件處理。模型格式錯誤是另一個輸入驗證情境，不能拿它當成頁面未掛載的唯一根因。
- `VISION-CENTER-SCRIPT-MOUNT` 驗證實際路由產生的 HTML 掛載模組一次；瀏覽器驗收另使用隔離的真實設定儲存，實際點擊保存、重新整理讀回、啟用／停用及無效輸入阻擋。
- 切換服務／模型不能把當次提交的額度與品質全部忽略。後端的目標 profile 是基礎，明確 payload 是覆寫；前端切換時只提交相對已載入表單真正改變的 profile 欄位，避免複製舊模型設定。停用草稿的部分欄位可保存，但已填欄位仍驗證安全性，不完整不得啟用或外送。`VISION-BOUNDARY-EDITS-DRAFT` 覆蓋首次切服務、返回舊模型同次修改、草稿及權限錯誤遮罩，另有瀏覽器流程驗收。
- 離線事件影片驗收必須核對預覽顯示的確實是同一素材影格。僅 API 有真實偵測，若 fixture 的舊 snapshot callback 仍回傳合成背景，也不能算完成。應檢查實際影像內容、尺寸與截圖，並將候選標籤放在框外避免遮住遠處小目標。私有素材與截圖只留本機 `data/diagnostics/`。
