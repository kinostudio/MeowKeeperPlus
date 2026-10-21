# 安全與隱私

## 1. 威脅模型

主要風險包括：

- Git、日誌、錯誤報告或截圖洩漏 Token 與攝影機帳密。
- 未授權 Telegram 使用者取得居家畫面或控制 PTZ。
- 將攝影機或本地控制 API 暴露到公網。
- ONVIF 工具意外修改影像、網路或 mydlink 設定。
- 事件影片無期限累積或被其他本機帳號存取。
- 相依套件、模型或 FFmpeg 二進位來源不可信。

## 2. 秘密管理

### 正式版

- 使用 Windows Credential Manager 或 DPAPI 保存秘密。
- `config.json` 只保存 `secret_id`。
- 秘密進入記憶體的時間越短越好，且不得序列化到報告。

### 開發期

- 使用程序環境變數或被 `.gitignore` 排除的 `.env`。
- `.env.example` 只含明顯假資料。
- 不將真實 Token 放在 CLI 參數；命令列可能被程序清單或 shell 歷史記錄。

### 遮罩

- Telegram Token 只可顯示少量前後字元或固定遮罩。
- RTSP URI 必須移除 userinfo，例如：
  `rtsp://***:***@192.0.2.10:554/path`。
- 例外與 FFmpeg stderr 送入日誌前先遮罩。

## 3. Telegram 授權

- Bot Token、Chat ID 及具名通知對象清單保存於 Windows Credential Manager／DPAPI；
  Dashboard 不回填 Token 或完整 Chat ID，只顯示遮罩、別名與是否啟用。
- 通知對象可由使用者逐一選擇；Host 啟動時只載入已勾選的對象。修改清單後必須經由
  受保護的「啟用 Telegram 並重新啟動 Host」動作套用，避免執行中的通知邊界被非預期改寫。
- 舊版僅有逗號分隔 Chat ID 的憑證仍可讀取；轉為具名清單時不會把既有秘密寫入 `config.json`。
- 除私人 onboarding `/start` 外，所有指令先比對 Chat ID 白名單。
- `/start` 唯一公開能力是回傳發送者自己的 Chat ID 與本機設定引導；只接受 private chat、正整數 sender/chat ID 相同且非 Bot。群組及身分不符不回應；不自動建立收件人、不授予任何監看／控制權限。回覆經既有有界 outbound queue，日誌仍只保留匿名參照，不能記錄 welcome 文字中的原始 Chat ID。
- `view_camera` 與 `control_camera` 分離。
- 管理指令必須有 `admin`。
- Callback Query 同樣執行權限檢查，不能只檢查斜線指令。
- 貓咪回饋 long polling 只在 Telegram 明確啟用且存在已選私人收件人時啟動；先唯讀檢查 `getWebhookInfo`，已有 webhook 即停用本地輪詢，絕不自動呼叫 `deleteWebhook`。回饋只接受白名單 Chat ID 的 private chat，並要求 sender ID 等於 chat ID、原事件具有同一 chat 的已保存 media message receipt。
- Callback payload 僅含事件 UUID 與 cat/not_cat 選擇；資料庫不保存原始 update、callback ID、Telegram User／Chat ID，只保存 SHA-256 參照、update offset、事件標籤及人工更正歷史。
- 新 Chat ID 預設無權限，需由本機 GUI 或既有管理者批准。
- 記錄控制動作的 Chat ID、時間、動作與結果，但不記錄 Token。

## 4. 攝影機與 ONVIF

Phase 0 允許的唯讀操作：

- 裝置探索與裝置資訊。
- Media Profile、串流 URI 與能力查詢。
- PTZ 能力、狀態與 Preset 清單。

不得自動執行：

- 解析度、FPS、編碼、碼率與 GOP 修改。
- 網路、DNS、Wi-Fi、帳號、密碼或時間設定。
- 韌體更新、重啟、重設與 mydlink 相關設定。

呼叫既有 Preset 或建立 Preset 需在 UI 顯示確切動作並由有權限使用者觸發。

## 5. 網路

- 不在路由器開啟 RTSP/ONVIF port forwarding。
- GUI 與服務 IPC 預設只監聽 loopback 或使用 Windows Named Pipe。
- 若未來提供 webhook，需明確的驗證、重放保護、速率限制與最小權限。
- Telegram 網路失敗不應阻塞本地監控。

## 6. 本地資料

- 資料庫、縮圖、影片與日誌位於專用資料目錄。
- 保留期限與最大容量可設定。
- 人工鎖定事件不可被自動清理。
- 低磁碟空間先停止一般事件影片，仍保存文字事件並告警。
- Dashboard 的完整資料備份使用密碼保護的 ZIP；密碼至少 6 個字元。
- 完整備份包含一般設定、SQLite 一致快照、Telegram 通知偏好、寵物 profile／參考照片／embeddings、
  以及已完成事件影片與截圖。Telegram 憑證只存在加密 payload，不出現在 manifest 或檔名。
- ring cache、preview cache、模型、FFmpeg、Python runtime 與暫存鎖檔不進入完整備份。
- 匯入前先驗證 AES-GCM、manifest、SHA-256 與路徑；校驗失敗不得寫入任何資料。
- 匯入成功後必須安全重啟 Host；不把未驗證的資料直接當成目前執行中的設定。

## 7. 供應鏈與授權

- 下載依賴、模型與 FFmpeg 前確認官方來源與完整性。
- Windows 發布包可內含 `vendor/ffmpeg/bin/ffmpeg.exe` 與 `ffprobe.exe`；必須同時
  保留 `vendor/ffmpeg/LICENSE`、版本說明與 SHA-256 紀錄，不能以不明來源二進位檔
  覆蓋。
- YouTube adapter 只接受公開頁面 URL；固定版 yt-dlp／yt-dlp-ejs 負責解析，不自行實作
  YouTube 解密。短期簽名串流 URL 只在單次 FFmpeg producer 的程序記憶體中使用，不輸出
  到設定、狀態 JSON、日誌或 Telegram caption。Cookie、OAuth、DRM、年齡／會員限制流程
  一律回報 `unsupported`，不得嘗試繞過。
- 預設 YOLO 權重只能由固定的官方 GitHub URL 取得，下載後比對 SHA-256；不接受任意
  使用者提供的模型下載 URL 作為自動化來源。`tools/fetch_model.py` 會將來源 repository、
  release tag、檔案大小、SHA-256、授權與 UTC 下載日期寫入 `models/model_manifest.json`；
  啟動時若 manifest 或權重不一致則停止使用預設模型。
- 鎖定可重現版本並產生第三方清單。
- 個人原型不代表可商業散布；V1.0 前審核 YOLO 框架、模型、資料集及所有依賴授權。
- V0.2 身分模型只接受官方 `facebookresearch/dinov2` checkpoint；固定 URL、commit、SHA-256、
  檔案大小與 Apache-2.0 授權寫入 `models/identity/dinov2_vits14/model_manifest.json`。
- 寵物參考影像、embedding、身份修正與事件影片視為本地敏感資料；不得上傳到模型服務或
  Telegram，除非使用者明確啟用事件通知。
- 攝影機場景參考照片、物件裁切與自訂標記名稱亦屬本機敏感資料；預設只留在本機。選用視覺 API 的場景參考需另外勾選並記錄同意，且隨事件圖片一起送出；隱私／排除區裁切不得加入拼貼。對外只送已選物件的裁切拼貼，不送原始參考照片或完整即時影格。
- DINOv2 低信心結果保留 `unknown`，不得以顯示名稱或最近一次 track ID 強行配對。

## 8. 安全驗收

- `git grep` 找不到真實 Token、密碼或 Stream Key。
- Probe 報告中的 RTSP URL 已遮罩。
- 未授權 Chat ID 無法取得快照、影片或 PTZ 控制。
- ONVIF 探針沒有寫入方法。
- 所有外部程序不使用 `shell=True` 且具逾時。

## 9. 開發與發布邊界

- 開發／建置工具不得未經明確操作偷偷修改 Python、GPU 驅動或 CUDA；缺少依賴時應顯示
  繁體中文修正提示並停止，不在背景執行安裝。
- Windows 可攜式包由建置者放入 Python runtime、必要套件、內含 FFmpeg 與已驗證模型；
  終端使用者不建立 venv、不執行 pip、不手動下載權重。
- 發布包建置會排除 `.env`、Token、RTSP 密碼、Stream Key、SQLite、事件影片、報告與
  私人測試資料，並以 `build_manifest.json` 保存檔案 hash。
- V0.2 寵物身分與行為結果尚未完成家庭實拍準確率驗收，文件與通知不得把 experimental
  候選描述成確定診斷。

## 10. Xiaomi 登入診斷

- go2rtc stdout/stderr 先經 pipe 即時遮罩，再寫入輪替日誌；讀取診斷時只允許有限尾端。
- Xiaomi response body、HTTP status、Content-Type 與 LoginError 只透過
  `XiaomiLoginDiagnostic` 的安全欄位保存；密碼、帳號、Token、驗證碼、cookie 與完整 URL
  不得落盤或進 Web API。
- 70016 且沒有 CAPTCHA／verification challenge 詳細資料時，分類為
  `upstream_api_changed`，不能翻譯成密碼錯誤。
- 登入失敗進入有限冷卻；冷卻期間不自動重試，必須由使用者明確解除後重新使用官方登入頁。

## 11. Dashboard 區網邊界

- Dashboard 預設只 bind `127.0.0.1`；只有使用者明確啟用
  `dashboard.lan_access_enabled=true` 並重新啟動後才 bind `0.0.0.0`。
- go2rtc API／RTSP、SQLite 與事件目錄不因 LAN 模式而暴露；瀏覽器只能透過登入後的
  同源 Dashboard API 取得狀態、JPEG、事件影片與診斷資料。
- Windows 防火牆流程只管理固定名稱 `MeowKeeper+ Dashboard LAN`，規則限定 TCP
  Dashboard Port 與 `Private` profile；不使用 `Any`／Public，不修改 go2rtc port。
- 私有 IPv4 清單只在受保護 API 動態計算，排除 loopback、169.254/16、未啟用與常見
  虛擬介面與 Tailscale 100.64.0.0/10；不寫入 config、SQLite 或日誌。
- 變更 bind 或 port 不會在執行中重綁；設定先原子保存並要求安全重啟，以免 Web 請求
  造成第二個 Host 或中斷既有媒體 ingest。
- `telegram_startup_notice` 預設關閉；只有已設定 Telegram、使用者明確啟用並重新啟動
  Host 後，才透過有界 outbound dispatcher 傳送摘要。摘要不含來源 URL、密碼、Token、
  Cookie 或其他秘密；`0.0.0.0` 只代表 wildcard bind，永遠不作為分享網址。

### 11.1 明確授權的 RTSP LAN 模式

- `go2rtc.rtsp_lan_access_enabled` 預設為 `false`；未明確啟用時 RTSP 與 API 都只綁
  `127.0.0.1`。
- 啟用後只允許 RTSP TCP `8554`，API `1984` 仍固定為 `127.0.0.1`，不建立 WAN、Public
  或任意 port 規則。go2rtc 只有在已確認固定 `MeowKeeper+ RTSP LAN` 規則後才可使用
  `0.0.0.0:8554`；規則限定 Windows `Private` profile、`localsubnet` 與已驗證的
  `go2rtc.exe` 路徑。
- 這個模式是刻意讓區網 RTSP client 繞過 Dashboard 登入；因此只可在可信任家庭 LAN 使用，
  不得做路由器 port forwarding。規則缺失或權限不足時 fail-closed，回退 localhost，
  不會先暴露再補防火牆。
# Phase 3 Web／Tray／遠端安全補充

- Web 預設只綁定 localhost；即使使用 Tailscale，也仍需應用層 token／登入驗證。
- `ApiAuthenticator` 使用 constant-time token 比較及獨立 CSRF header；未設定 token 時操作 API
  不開啟。秘密不得進 HTML、URL、SQLite 一般欄位、日誌、Tray 或診斷報告。
- Tray 只接受 `127.0.0.1`、`localhost` 或 `::1`，停止／重啟需要二次確認；Tray 退出不停止
  背景核心。
- 事件影片只能從已知事件根目錄內讀取；API 不暴露整個 `data/events`。
- Tailscale 只允許官方 CLI 狀態／明確確認 Serve 計畫，不自動登入、安裝、使用 Auth Key、
  Funnel 或修改 ACL。Xiaomi 帳號只能由使用者在官方流程輸入；go2rtc 管理介面只綁 localhost。
- Xiaomi MISS PTZ 實驗介面預設關閉；只有已登入 Dashboard、CSRF 通過且使用者明確按下
  方向或停止按鈕時才可送出 allow-listed command。官方 go2rtc binary 不會被此功能替換。
