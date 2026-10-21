# Xiaomi 攝影機後端 Region／MIoT 接通 SOP

> 歷史證據文件：下列成功／失敗限於所標日期、裝置與執行環境，不代表目前狀態。現行防重犯規則見 [經驗手冊](LESSONS_LEARNED.md)。升級由使用者執行；不得由本文件推導自動安裝或 UAC 授權。

2026-09-24 的 SYSTEM 防火牆、內附 FFmpeg JPEG 後備路徑與升級占用教訓，另見 [Xiaomi SG／JPEG 與 SYSTEM 升級 SOP](XIAOMI_SG_JPEG_SYSTEM_UPGRADE_SOP.md)。

狀態：2026-09-26 重新複核後，`chuangmi.camera.026c02` 已由正式 go2rtc producer=1 持續輸出，內附 FFmpeg 與 Dashboard `/live` 均取得會變動的真實 JPEG；`isa.camera.hlc7` 仍未完成影格驗收。這是後端 API region 選擇，**不是**要求使用者把米家 App 的「地區」改成新加坡。

## 必須記住的區別

- 米家 App 顯示的地區、go2rtc `xiaomi://...:<region>@...` 中的後端 region、實際串流中繼所在域名，是不同概念。不得寫死「台灣＝SG」。
- go2rtc 官方 v1.9.14 支援 `cn`、`de`、`i2`、`ru`、`sg`、`us`。同一 DID 可以在不只一個 region 的 `device_list_page` 中出現；**能列出裝置不代表該區域能開流**。
- 目前 `cn` 可列出兩台目標裝置，但既有 Session 查詢狀態為 `isOnline=false`；同一 DID 在 `sg` 為 `isOnline=true`。Region 必須以同一 DID 的實際可用性驗證，不能由帳號國別、App 語言或裝置名稱推測。
- 背景自動探索依序讀取官方支援的 region；先驗證 DID／model 對應，再以一個暫時 `subtype=sd` stream、一個 JPEG consumer 確認真的在線。成功後用 DID 與帳號參照的雜湊鍵記住該攝影機 region；不保存 DID、Token、完整 `xiaomi://`、臨時媒體網址或影格。已記住的 region 在後續刷新直接沿用，避免重複開流；若日後該區域失效，需重新探索並更新 cache，目前尚未實作自動失效切換。

## 本次原始觀察與錯誤點

| 步驟 | `cn`／錯誤路徑 | `sg`／可用路徑 |
| --- | --- | --- |
| `device_list_page` | 同一 DID 可列出；`isOnline=false` | 同一 DID 可列出；`isOnline=true` |
| 官方 go2rtc Xiaomi P2P | `miss_get_vendor` HTTP 200、Xiaomi `code=-6`、`result` 缺失；producer=0 | `.55` 使用官方 v1.9.14、單一 `subtype=sd` stream，`/api/frame.jpeg` HTTP 200，51,857 bytes 有效 JPEG |
| MIoT `.55` start action | 在 `cn` 的既有測試失敗，內層 `-704042011` | SIID 8／AIID 1、`in=[1]`：外層 HTTP 200／code 0，內層 code 0，回傳臨時 RTSP 位址 |
| MIoT `.56` start action | SIID 9／AIID 1 既有測試失敗 | 回傳 HTTPS `.m3u8`，但約 22 秒內持續 HTTP 404；尚未接通 |
| `.55` 臨時 RTSP | 位址字面上是 `rtsp://`，但 `:443` 明文 RTSP `OPTIONS` 逾時 | 同一端點經 TLS 後 RTSP `OPTIONS` 回 200；記憶體內改用 `rtsps://` |
| 解碼 | OpenCV 內建 FFmpeg 的 Windows TLS 回 `0x8009030e`，25 秒沒有影格 | 專案內附 FFmpeg 8.1.2／GnuTLS，以 stdin 傳入 ffconcat 來源，退出碼 0，擷取 54,896 bytes 有效 JPEG；隔日再次取得 52,114 bytes、640×360 JPEG |

2026-09-20 的單張影像在另一個可寫工作目錄 `outputs/xiaomi-chuangmi-proof-2026-09-20.jpg`，SHA-256：`dc102a46cd7200ab6e6f76056ff4afc53bed98f210992d73ce5aeea1f9c0c7d5`。該影像是私人監控資料，**不得複製進 repository 或發布包**。

## 操作／診斷順序

1. 確認僅一個官方 go2rtc 實例、API／RTSP 皆綁 `127.0.0.1`，`GET /api/xiaomi` 顯示一個已登入帳號；不要再次提交帳密。
2. 對每個候選 region 讀 `GET /api/xiaomi?id=<帳號參照>&region=<region>`。從同一次回應解析 DID、model；僅在程序記憶體保留完整來源。
3. 以「帳號參照＋DID＋model」建立不含原值的裝置鍵。首次探索依 `cn,de,i2,ru,sg,us` 搜尋；已驗證 cache 命中時直接沿用。僅測出現在該 region 列表的同一 DID／model。
4. 每個候選最多建立一個暫時 go2rtc stream，固定 `subtype=sd`；呼叫一次 JPEG consumer，檢查 HTTP 200 與 JPEG SOI／EOI。無影格或逾時就刪除暫時 stream，轉下一個 region；不得做 profile sweep 或並行開流。
5. 只有取得真實影格才將 region 記入不含秘密的 cache，並讓正式既有 go2rtc bridge 使用該 region 的 `xiaomi://` 來源。正式 Dashboard 仍只讀 localhost restream，不向攝影機另拉第二條串流。
6. 若所有候選都失敗，正式 Host 預設只在同一 DID／model 確實出現在 `sg` 清單時顯示「SG 候選，影格未驗證」，提供一次 `subtype=sd` 畫質測試；此狀態**不會**寫入已驗證區域快取、也不能直接接入。可用 `--xiaomi-fallback-region none` 停用候選，或選其他支援的候選 region。這不是「台灣＝SG」的固定映射；只有拿到真實影格才算驗證。保留診斷分類：清單沒有 DID、清單有 DID 但開流失敗、影格逾時、雲端拒絕等。
7. 驗證正式 Dashboard：已登入頁面顯示最新影格，來源只有一個 producer；測試完成移除暫時 stream。若未驗證正式頁面，不得宣稱整合完成。

## 這次 Dashboard 接線踩到的錯誤

- 冷啟動時，畫質測試原先先呼叫 go2rtc `/api/streams?video=all...`，其 3 秒 API 逾時可能在 FFprobe 取得影格前就將可用攝影機判為失敗。已改為以專案內附 FFprobe 作單一有限時影像驗證；不額外先開全軌道探測。
- 測試環境對 AppData 的寫入可能受限。區域 cache 優先寫 AppData；若無法寫入，回退至 `data/state/xiaomi-regions.json`。兩處只保存雜湊裝置鍵與 region；應以重啟後仍選到同一區域驗證持久化。
- 本輪修正後，`.55` 的單一 `subtype=sd` FFprobe 回傳穩定、640×360、HEVC；內附 `vendor/ffmpeg/bin/ffmpeg.exe` 從隔離 go2rtc 的 localhost RTSP 取得一張 52,182 bytes 有效 JPEG，暫時 stream 已移除。這是後端解碼驗證，不等於 Dashboard 已顯示預覽。
- 單一 ingest 初測：`FfmpegMediaIngest` 從同一個 localhost RTSP 來源取得 20 張有效 JPEG（設定 5 FPS），同一 FFmpeg 程序寫出 MPEG-TS 分段；測後 go2rtc 暫時 stream 數量回到 0。後續延長測試發現分段索引時間錯誤，詳見下項。
- 真實 RTSP 分段不保證每秒準時切割：FFmpeg segment muxer 會等待關鍵幀。舊索引將每個檔案硬排成恰好 1 秒，導致最近分段的時間戳落後於實際時鐘，`get_recent_clip(5)` 回報「沒有可組片的影音分段」。已讓即時來源依已完成檔案的修改時間建立索引，寫入中的最新分段暫不納入，程序結束後再掃描。單元測試涵蓋不規則完成時間及寫入中檔案。
- 修正後以 `.55` 真實串流再次取得 45 張有效影格、3 個已索引分段；`get_recent_clip(5)` 成功組出可讀 MP4，實際 5.998 秒、106,087 bytes。另以**模擬事件觸發**驗證 SQLite `clip_status=completed`、`telegram_status=disabled`、MP4 檔案存在、schema version 3。兩次測後臨時 go2rtc stream 均回到 0。這證明後端取流／快取／組片，但不等於真實 YOLO 貓咪事件、Dashboard 預覽或 Telegram 對外通知已驗收。
- 同一台攝影機的單張真實 JPEG 已在記憶體內交給預設 `models/yolo11n.pt`；模型成功載入，本機實際後端為 CPU，該張影格沒有偵測到貓咪，因此**不是**含貓正例、事件或 Telegram 驗收。沒有保存私人影格或發送外部通知，暫時 stream 已移除。
- `--disable-monitor` 只禁止 Host 啟動時自動監控；在 Dashboard 按「接入 MeowKeeper+」仍會啟動來源。測試時使用 `--analyzer noop` 避免觸發貓咪通知，切勿誤以為前者禁止所有後續接線。

## 安全及回復

- 不修改米家 App 地區、攝影機韌體、網路設定或正式 go2rtc binary；不套用 PR #2264。
- 不把 Token、passToken、Cookie、DID、完整 IP、完整 `xiaomi://` 或臨時媒體網址寫入 config、DB、報告、日誌或命令列。輸出只保留 region、model、HTTP／動作狀態、是否有影格、大小與 SHA-256。
- MIoT 臨時位址診斷時，啟動後必須在 `finally` 呼叫 stop：`.55` SIID 8／AIID 2；`.56` SIID 9／AIID 2。停止失敗只可有限重試並告警，不能留下無限背景程序。
- 24H 正式路徑優先沿用已驗證的官方 go2rtc 本地 restream。MIoT 臨時位址與 `rtsps://`／GnuTLS 僅是獨立診斷證據；除非另做生命週期、安全與斷線測試，不得直接寫入正式 source config。
- `code=-6` 不能在沒有比較正確 region 與影格結果時直接歸類為帳號拒絕或機型不支援；看到 `no p2pid allocated` 也不能盲目重啟攝影機或套 PR。

## 2026-09-26 最新實機複核

- `.55`（`chuangmi.camera.026c02`）在正式 go2rtc v1.9.14 維持單一 producer；repository `.venv` Host 只建立 localhost RTSP consumer，內附 FFmpeg 取得約 65 KB JPEG，等待 6 秒後影像 SHA-256 改變。Dashboard `/live` 已實際顯示該影像。這證明目前產品的直接 FFmpeg JPEG 路徑可用，不能把 go2rtc `/api/frame.jpeg` 的 500 當成唯一影像來源。
- `.56`（`isa.camera.hlc7`）以 SG、單一暫時 stream 依序測試 `subtype=sd`、`hd`、`auto` 及 `transport=tcp`，均未建立可供 RTSP DESCRIBE 的影像；其中 `/api/frame.mp4` 的遮罩錯誤為 `read udp [::]:<臨時埠>: i/o timeout`。所有暫時 stream 均已刪除。
- 歷史測試當時沒有留下 HLC7 防火牆規則；建立「只允許目前 go2rtc.exe 接收 `.56` UDP」的暫時規則因工作階段沒有系統管理員權限而回 `Access denied`。那一輪不得當作目前狀態。
- 2026-09-26 使用者明確建立並保留一條固定規則 `MeowKeeperPlus HLC7 UDP permanent`：Public profile、UDP、RemoteIP 僅為 `<camera-ip-redacted>`、Program 僅為已安裝 `C:\ProgramData\MeowKeeperPlus\App\vendor\go2rtc\v1.9.14\go2rtc.exe`。此規則不代表影像已驗收，且不得自動擴大範圍。
- 2026-09-26 規則建立後只做一次 `subtype=sd` HLC7 重試：stream PUT HTTP 200，但 JPEG API HTTP 500；producer 只保留來源描述、未協商 video media。接著在**同一個臨時 localhost RTSP stream** 上，以內附 FFmpeg（TCP、單一 consumer）成功解碼 640×360 有效 JPEG；FFmpeg 有 HEVC reference-frame 警告，但輸出 JPEG 可由 FFprobe 讀取。這證明 HLC7 的影格路徑可用，問題在 go2rtc JPEG API／FFprobe 首幀處理，不是完全沒有串流。測試 stream 已刪除，正常 `.55` stream 仍維持 producer=1、consumer=2。
- 因此 `profile_probe` 現在在 FFprobe 無法讀取時，會對同一暫時 stream **序列化**呼叫內附 FFmpeg、驗證 JPEG SOI／EOI 與尺寸後才標記 profile stable；不增加攝影機連線，也不做 profile sweep。Dashboard 正式 Host 尚待下一輪重新載入區域、按一次 SD 測試與接入驗收。
- 下一個合法診斷邊界是取得新的 HLC7 UDP 回程證據或上游修正；在此以前不要重複掃描 profile、重登入、套用 PR #2264 或修改 go2rtc binary。

## 尚未驗收

- 2026-09-24 初次測試時，SG 清單包含兩台目標裝置，`.55` 的暫時 SD stream PUT 成功，但開流 HTTP 500，回應為 `read udp [::]:<臨時埠>: i/o timeout`；JPEG API 回 HTTP 200、0 bytes。主機到攝影機 ICMP 可達。Windows 目前是 Public 防火牆，既有 go2rtc UDP 放行規則指向 repository 的舊執行檔，與目前 `C:\ProgramData\MeowKeeperPlus\App\vendor\go2rtc\v1.9.14\go2rtc.exe` 不同。當時尚未取得新 JPEG，不能從 SG 裝置清單直接宣稱串流已接通。
- 2026-09-24 後續取得使用者明確授權後，以 Windows UAC 建立**臨時** Public 入站 UDP 規則，僅允許目前安裝版 go2rtc.exe 與 `.55` 攝影機通訊；`netsh` 已核對規則的程式、UDP、來源 IP 與 Profile。原 `read udp ... i/o timeout` 不再出現。go2rtc 的 `/api/frame.jpeg` 仍回 HTTP 500、未產生影格，回應與 FFmpeg 有關；直接呼叫內附 FFmpeg 的第一次測試誤用了本機不接受的 `-rw_timeout`，該次退出**不能**當攝影機失敗證據。移除該參數後，內附 FFmpeg 從**同一條 localhost RTSP 暫時 stream** 取得 54,113 bytes 的有效 640×360 JPEG，SHA-256 `23d806e4b50f181378ab33efff51f968efeaba3cae7f3f57f39ad7ca59dc262d`；影像未保存，暫時 stream 已刪除。測後已用 UAC 移除臨時防火牆規則，`netsh` 確認不存在。這證明 SG `.55` 串流在該臨時網路條件下可用，**不等於目前安裝版 Dashboard 已持續顯示畫面**。程式已增加 go2rtc JPEG API 失敗時的內附 FFmpeg 後備影格驗證，但仍待新版安裝及持續入站權限驗收。

- `.56` 的實際 JPEG／影片；其 MIoT HLS 位址仍 404。
- 真實 `.55` 已在隔離 TestClient 的受保護 Dashboard API 取得 JPEG 與具 `ftyp` 標頭的 `/live` MP4 回應（本次 API 回應未另跑 FFprobe），單一 producer／consumer 且清理後回到 0；詳見 [遮罩 smoke 證據](../reports/xiaomi-dashboard-api-smoke-2026-09-20.json)。仍需正式 Host 的已登入瀏覽器畫面、真實 YOLO 貓咪事件與 Telegram 對外通知端到端；隔離 API smoke 不可代替這些驗收。
- 12–24 小時重啟與 region cache 失效後的恢復。

## 2026-10-04 C701 遠端區網診斷

- `chuangmi.camera.079ae2` 位於另一住處；目前單一 Xiaomi Session 在 `sg` 可列出此 model，來源主機與使用者提供的遠端攝影機 IP 相符。ICMP 經 Tailscale 子網路由可達，但這**不能**證明 CS2 的 UDP 雙向握手可用。
- 對正式 go2rtc v1.9.14 僅建立一個臨時 stream、一個 FFmpeg consumer，依序測 `subtype=1` 與 `subtype=3`：兩者皆 RTSP 404、未建立 active producer、未取得 JPEG。FFmpeg 404 是上游 producer 無法建立的結果，不是 HEVC／JPEG 解碼錯誤。
- 使用者在系統管理員 PowerShell 執行 `scripts/Test-C701Jpeg.ps1`，暫時允許**已安裝 go2rtc.exe＋此遠端攝影機 IP＋Public UDP**；`reports/c701-jpeg/probe-20261004-005930.json` 記錄單次 `subtype=1` 仍失敗。已核對該臨時規則不存在、測試 stream 已刪除。正式 go2rtc 日誌於同次測試顯示 `read udp [::]:<臨時埠>: i/o timeout`。
- 再明確指定 `transport=tcp`、`subtype=1` 單次測試，仍在 CS2 前置 UDP 握手逾時；`transport=tcp` 並不代表這個握手階段不使用 UDP。所有臨時 stream 均已清除，原有兩條 stream 保持不變。
- go2rtc [官方 Xiaomi 文件](https://github.com/AlexxIT/go2rtc/blob/master/internal/xiaomi/README.md) 明確註記攝影機連線只支援 local。跨住處的 Tailscale 子網路由雖可到達 IP，仍不能視為已通過本地 P2P 握手。下一個可驗證方案是確認遠端 Tailscale 節點是否與 C701 同一 LAN，然後在**遠端 LAN** 隔離執行 go2rtc、取得真實 JPEG，再評估以單一受限 RTSP 中繼送回 MeowKeeper+；不得在尚無遠端節點與影格證據時宣稱接通。
- 使用者確認遠端 Tailscale 節點是與 C701 同一 LAN 的 Windows 電腦。已建立 `scripts/remote-c701/` 隔離測試腳本與 `dist/C701-remote-relay-v1.9.14-2026-10-04.zip`；包內官方 go2rtc v1.9.14、內附 FFmpeg，無登入 Session、Token、DID 或私人 JPEG。ZIP SHA-256 為 `5CA140CD481659F3AD5B7FA32F097466E6EAD92E6BDA5963CBB333420F648F3A`，Windows PowerShell 5.1 三支腳本語法皆通過。測試器 FFmpeg 必須連遠端 go2rtc 實際綁定的 Tailscale IP `:28554`，不得誤連未監聽的 `127.0.0.1:28554`。**遠端電腦尚未執行，故 JPEG 與 RTSP 回傳尚未驗收。**
- 遠端首次執行的安全化報告：`account_count=1`、`model_match_count=1`，但在記錄 `stream_put` 成功前出現 `WebException`，尚未進入 FFmpeg，沒有 JPEG。這只能定位到讀取 `/api/streams` 或 PUT 階段，不能歸因於解碼、P2P 或 C701 不支援。隔離設定中有 Xiaomi Session，無 `c701_sd` stream；原測試報告缺 HTTP status。已在診斷版腳本加 `stage`、HTTP status、Content-Type、response 長度／SHA-256 與白名單錯誤類別，不保存原始回應或秘密。`dist/C701-remote-relay-v1.9.14-2026-10-04-diagnostic-2.zip` SHA-256：`AD332A3936DC71EB431C5EAA0F665F5A167C057E117193770C3A71183A6932E1`。**此診斷版尚待遠端執行。**
- 診斷版第二次回報停在 `stage=stream_list`，`account_count=1`、`model_match_count=1`、`error_type=RuntimeException`；因此並非遠端 FFmpeg/JPEG 問題，也未能證明 PUT 失敗。舊版用 PowerShell `Invoke-RestMethod` 直接解析可能為空的 `/api/streams` 回應，卻沒有記錄其 HTTP status。診斷版 3 改用 `Invoke-WebRequest` 記錄 status，再明確處理 `null`／空清單或 JSON 物件；仍只在確認無既有 `c701_sd` 後建立一個 stream。`dist/C701-remote-relay-v1.9.14-2026-10-04-diagnostic-3.zip` SHA-256：`F4CE9BEB853CE898A0C3035484AF92F4373B5DBBAA2D964BC8B3F8294DF1C365`。**尚待遠端驗證**，不能把 PowerShell 空清單假設寫成已證實根因。
- 後續遠端回報已證明 `GET /api/streams` 為 HTTP 200 且有 `c701_sd`；早先 `stream_list` 失敗是測試腳本的空值解析問題。單一已登錄 `c701_sd` 的來源為 SG、同一次裝置清單的 C701、`subtype=1`，go2rtc API/RTSP 為同一 PID；遠端 FFmpeg 的 RTSP DESCRIBE 回 404，沒有 JPEG。進一步 `GET /api/streams?src=c701_sd&video=all` 回 HTTP 500、body 0 byte；`GET /api/frame.jpeg?src=c701_sd` 回 HTTP 200、0 byte。這只能證明目前未取得媒體，不可歸因於 HEVC 解碼或 Tailscale RTSP 轉送。測試腳本額外加入的 `audio=0` 並非官方 Xiaomi 文件範例參數；已準備 `scripts/remote-c701/Probe-C701Canonical.ps1`，用同一已登入 SG 裝置來源，依序比較「官方清單原始 URL」及只加 `subtype=3`，每次只有一個暫存 stream 並測後移除。**這項對照尚待遠端執行，不得宣稱接通。**
- 第一次 canonical 對照回報兩個 profile 均在 `PUT /api/streams` 得 HTTP 400，尚未進入取流，且 `final_temporary_stream_absent=false`。依 [go2rtc v1.9.14 原始碼](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/internal/streams/api.go)，PUT 先建立記憶體內 stream，再呼叫 `PatchConfig`；因此 400 可能在持久化階段發生，不能把它當作 Xiaomi 來源格式錯誤，更不能把該次測試當成畫質失敗。已修正診斷腳本：只清除名稱為 `c701_canonical_probe` 的前次暫存 stream，改用不寫 config 的 PATCH，再逐項 probe/frame，測後刪除並確認不存在；正式 `c701_sd` 不動。**修正版仍待遠端執行。**
- 修正版 canonical 對照已由遠端執行：前次殘留暫存串流已移除；官方裝置清單原始 URL 與只加 `subtype=3` 均 `PATCH HTTP 200`，但各自的開流 probe 為 HTTP 500、`frame.jpeg` 為 HTTP 200／0 byte；兩個暫存串流都已移除，最終不存在。這排除「僅因測試腳本額外 `audio=0` 或 C701 的 subtype=1 才失敗」這個簡單假設；仍不能推論為解碼故障或雲端失敗。`scripts/remote-c701/Probe-C701FailureStage.ps1` 只以一次原始來源 probe，從 PowerShell ErrorDetails／HTTP response 與新增 log 擷取**分類後**錯誤，不輸出原文或秘密；尚待遠端執行。
- 故障階段腳本已在遠端執行：同一 SG Session 有一個 C701；`PATCH HTTP 200`，開流 `HTTP 500`，PowerShell ErrorDetails 中 42-byte 錯誤歸類為 `udp_or_io_timeout`，約 5.49 秒失敗；in-memory producer=1、consumer=0，暫存串流已刪。這定位到開流時的 UDP 握手／回應階段，而不是 FFmpeg HEVC 解碼；目前**尚不能證明是 Windows 防火牆**。上游 [go2rtc issue #2424](https://github.com/AlexxIT/go2rtc/issues/2424) 有相同 `read udp ... i/o timeout` 且攝影機 ICMP 可達、最後發現主機防火牆擋下來源埠不同的 UDP 回覆的案例，但機型與作業系統不同，只能作為下一步 A/B 假設。已準備 `scripts/remote-c701/Probe-C701UdpFirewall.ps1`：預設唯讀，確認官方 binary、隔離 process、同區網出口與防火牆現況；只有明確選 TemporaryRule 且具管理員權限時，才建立限於該程式、該攝影機 IP、該 LAN 本機 IP、目前網路類型的臨時 UDP 入站規則，單次測試後於 finally 移除。**規則測試尚未執行。**
- **遠端 C701 第一張真實 JPEG 已驗證（使用者回傳遠端腳本結果）：** 唯讀預檢確認官方 go2rtc PID `71308`、攝影機出口為同一 LAN 而非 Tailscale、遠端網卡為 Public、Windows 防火牆啟用、原先沒有此 go2rtc 的 UDP 入站允許規則。一次性 `TemporaryRule` A/B 將規則限於已驗證 go2rtc.exe、C701 的 LAN IP、遠端 LAN 本機 IP 與 Public；建立後同一 `c701_sd` 的開流 probe 與 JPEG API 均 HTTP 200，JPEG magic 與影像解碼驗證通過，**848×480、38,959 bytes**。腳本回報 `firewall_rule_created=true`、`firewall_rule_removed=true`，臨時規則已移除。與無規則時 5.49 秒 UDP timeout 對照，足以將該遠端環境的阻塞定位於 Windows UDP 入站規則。此為**臨時取一張影格成功**，不是 10 分鐘串流、Tailscale RTSP 端到端或 24H 監控驗收；若要持續使用，需另經使用者同意建立常駐限縮規則，並驗證本地主機取得遠端 RTSP 影格。不要改動小米帳號地區、正式 go2rtc 或 MeowKeeper+ 串流核心。
- **持續使用的下一步（尚待遠端執行）：** 使用者已同意在 C701 所在 Windows 電腦建立常駐限縮 UDP 入站規則。`scripts/remote-c701/Set-C701UdpFirewall.ps1 -Mode Install` 先核對官方 binary SHA-256、隔離程序、同 LAN 出口與 Public profile；只允許該執行檔接收 C701 `192.168.18.62` 發往當前 LAN 本機 IP 的 UDP。腳本建立新規則後取一張 JPEG；若失敗，回滾新規則。`-Mode Status` 可唯讀確認，`-Mode Remove` 僅撤銷完全相符的同名規則。此段描述的是操作方案，**不得在看到遠端安裝輸出前寫成已建立**。

## 2026-10-05 C701 遠端中繼與本機來源邊界

- 使用者後續回報：遠端 `Set-C701UdpFirewall.ps1 -Mode Install` 為 `installed_and_verified`，go2rtc stream/JPEG 均 HTTP 200，單張有效 JPEG 為 848×480、40,460 bytes。這證明遠端電腦在其 LAN 可取得 C701 畫面；不是 MeowKeeper+ 正式 Host 已接入的證據。
- 本機到遠端 Tailscale 節點的 RTSP TCP 埠可達。本機內附 FFprobe 對遠端單一 `c701_sd` stream 回報 HEVC、848×480；內附 FFmpeg 取得有效 JPEG。修正 `FfmpegFrameSource` 持續排空有界 stderr 後，該類別在同一路徑單次取得有效 JPEG，約 5.5 秒完成。HEVC 警告可能使未讀取的 stderr pipe 填滿，造成原先等待；修正的是本機解碼程序的死鎖風險，不是 Xiaomi 登入或 P2P。
- 正式安裝包 r50 已通過 `/healthz`，但目前正式監控來源仍是本地 `026c02` 的單一 RTSP 來源。Xiaomi 設定精靈的「測試畫質」仍走**本機** go2rtc 直連 C701；對異地攝影機顯示 SG 候選與 FFprobe 失敗，不能推論遠端中繼失效。正確接線是由「來源設定」選 RTSP、使用遠端 go2rtc 的受限 Tailnet 中繼；切換前需先確認是否允許取代目前唯一來源。
- 上述是短時取流驗證，尚未完成正式 Host 即時畫面、事件／AI、10 分鐘連續取流或 24H 驗收。官方 go2rtc 的 Xiaomi MISS PTZ 實驗版未啟用；一般 RTSP 中繼本身不提供 PTZ 控制。
- 本機 `026c02` 的正式來源為 `127.0.0.1:8554/xiaomi_camera_2`，同名 go2rtc 活動 producer 回報 `xiaomi/miss`、`CS2 (chuangmi.camera.026c02)`。若 Host 重啟或另一台裝置的畫質探測失敗，舊精靈只看暫存的 `selected` 狀態，會把已顯示影格的本機來源誤判為「未配對」並鎖住即時畫質按鈕。修正後只在**目前來源 URL 為本機 go2rtc、stream 名稱一致、活動 producer 型號一致**時恢復配對；遠端 RTSP 不會啟用本機 Xiaomi 畫質／PTZ 控制。這是配對邏輯與單元測試通過的結果，正式 Host 仍須升級後於已登入頁面驗收按鈕及實際影格切換。
- 2026-10-05 `r52` 可攜包已建置並通過 19,062 個檔案的 manifest／模型／私人資料驗證；366 項測試、Ruff、compileall 通過。自動升級嘗試時 UAC 在非互動執行環境被取消；本輪後續唯讀核對發現正式 ProgramData Host 已變為 `r51` 且 `/healthz` 正常，來源仍為本機 `xiaomi_camera_2`。此變化不得歸因於已取消的自動安裝，也**不是 r52 已安裝**。下一步需在可見的管理員工作階段升級 r52，再驗證本地畫質按鈕、實際 subtype 影格與 C701 遠端 RTSP 是否需要取代現有唯一來源。

## 2026-10-06 C701 中繼停在 stream_list 的處置

- 遠端舊版 `Test-C701Relay.ps1` 在 `/api/streams` 已含 `c701_sd` 時一律丟出 `RuntimeException`，報告僅顯示 `stage=stream_list`／`stream_or_frame_failed`。這不是攝影機、Xiaomi 登入或 JPEG 解碼失敗的證據；先前已發生過相同誤判，不應再要求使用者反覆按本機設定精靈的 SD 測試。
- 同輪遠端唯讀回報：`GET /api/streams` HTTP 200 且 `c701_sd` 存在；對該 stream 開流與取 JPEG 均在 12 秒內無 HTTP 回應。本機 Tailnet RTSP TCP 埠可達，但 FFprobe 無法取得有效媒體。必須先恢復遠端中繼影格，才可切換正式 Host 的唯一來源；不可因 TCP 可連就取代正在工作的本地攝影機。
- 首版修補仍在 `stream_list` 停住：go2rtc 對尚未啟動的 producer 只回傳 `url`，沒有 `format_name`／`user_agent`；不能把缺少後兩欄解讀成攝影機離線。現行腳本只在 `c701_sd` 恰有一個 producer、零 consumer，且其記憶體內 URL 與同輪 SG 裝置清單的來源 base／DID／model／`subtype=1` 相符時沿用既有 stream，不刪除、不重新 PUT；不輸出來源原值。身份不符時拒絕操作。
- 遠端重跑後已通過上述來源比對，但該電腦以 FFmpeg 連**自身 Tailscale RTSP 位址**的 35 秒測試停在 `ffmpeg_frame`，沒有退出碼。這一項單獨不能判定攝影機待機；需與本機到遠端中繼的實測分開看。
- 同日從 MeowKeeper+ 電腦直接連遠端 Tailnet RTSP `c701_sd`：TCP 28554 可連，內附 FFprobe 讀到 HEVC、848×480；內附 FFmpeg 取得一張有效 JPEG（46,260 bytes），人工檢視為真實房間畫面；私人 JPEG 僅存本機暫存目錄，未加入 repository。另以單一 FFmpeg consumer 持續取流，10 秒輸出 142 影格、退出碼 0。HEVC 起始參考影格曾有警告，但不妨礙後續取得畫面。因此本輪證實遠端 C701 **當下可經 Tailscale 提供影格**；遠端腳本的自身位址逾時原因尚未定位，不能說攝影機已關機，也不能把此短測當作正式 Host／24H 驗收。
- **常駐腳本首次執行的錯誤與修正：** 遠端回報 `mode=Install`、官方 PID 與同 LAN／Public 預檢均通過，但停在 `ParameterBindingArgumentTransformationException`；輸出中的 `rule_status=absent` 是建立前狀態，不能證明 `New-NetFirewallRule` 沒有成功。查出腳本在建立後驗證時把單元素陣列傳給 `-AssociatedNetFirewallRule`，該參數需要單一規則物件。已改為先檢查陣列數量，再取唯一物件查詢；使用 mock 規則完成函式驗證。下次執行必須先讀取並精確核對同名既有規則，避免重複建立。**目前仍未取得遠端修正版的輸出，不得宣稱常駐規則或持續影像已驗收。**
