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

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。

## 尚未驗收

- 2026-09-24 初次測試時，SG 清單包含兩台目標裝置，`.55` 的暫時 SD stream PUT 成功，但開流 HTTP 500，回應為 `read udp [::]:<臨時埠>: i/o timeout`；JPEG API 回 HTTP 200、0 bytes。主機到攝影機 ICMP 可達。Windows 目前是 Public 防火牆，既有 go2rtc UDP 放行規則指向 repository 的舊執行檔，與目前 `C:\ProgramData\MeowKeeperPlus\App\vendor\go2rtc\v1.9.14\go2rtc.exe` 不同。當時尚未取得新 JPEG，不能從 SG 裝置清單直接宣稱串流已接通。
- 2026-09-24 後續取得使用者明確授權後，以 Windows UAC 建立**臨時** Public 入站 UDP 規則，僅允許目前安裝版 go2rtc.exe 與 `.55` 攝影機通訊；`netsh` 已核對規則的程式、UDP、來源 IP 與 Profile。原 `read udp ... i/o timeout` 不再出現。go2rtc 的 `/api/frame.jpeg` 仍回 HTTP 500、未產生影格，回應與 FFmpeg 有關；直接呼叫內附 FFmpeg 的第一次測試誤用了本機不接受的 `-rw_timeout`，該次退出**不能**當攝影機失敗證據。移除該參數後，內附 FFmpeg 從**同一條 localhost RTSP 暫時 stream** 取得 54,113 bytes 的有效 640×360 JPEG，SHA-256 `23d806e4b50f181378ab33efff51f968efeaba3cae7f3f57f39ad7ca59dc262d`；影像未保存，暫時 stream 已刪除。測後已用 UAC 移除臨時防火牆規則，`netsh` 確認不存在。這證明 SG `.55` 串流在該臨時網路條件下可用，**不等於目前安裝版 Dashboard 已持續顯示畫面**。程式已增加 go2rtc JPEG API 失敗時的內附 FFmpeg 後備影格驗證，但仍待新版安裝及持續入站權限驗收。

- `.56` 的實際 JPEG／影片；其 MIoT HLS 位址仍 404。
- 真實 `.55` 已在隔離 TestClient 的受保護 Dashboard API 取得 JPEG 與具 `ftyp` 標頭的 `/live` MP4 回應（本次 API 回應未另跑 FFprobe），單一 producer／consumer 且清理後回到 0；詳見 [遮罩 smoke 證據](../reports/xiaomi-dashboard-api-smoke-2026-09-20.json)。仍需正式 Host 的已登入瀏覽器畫面、真實 YOLO 貓咪事件與 Telegram 對外通知端到端；隔離 API smoke 不可代替這些驗收。
- 12–24 小時重啟與 region cache 失效後的恢復。

## 2026-10-04 C701 遠端區網診斷

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。

## 2026-10-05 C701 遠端中繼與本機來源邊界

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。

## 2026-10-06 C701 中繼停在 stream_list 的處置

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。
