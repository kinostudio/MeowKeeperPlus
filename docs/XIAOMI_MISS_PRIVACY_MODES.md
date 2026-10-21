# Xiaomi MISS 方向控制與隱私選項

## 模式語意

- `position`：轉到使用者親自確認的隱私位置，不停止攝影機、不停止錄影，也不代表物理遮罩。
- `standby`：攝影機本身關閉／待機；不得用停止 AI、來源中斷或 PTZ 移動冒充此模式。
- C701 的公開 MIoT v2 規格列出 `camera-control` service 2、`on` property 1，bool，R/W/N：
  https://home.miot-spec.com/spec/chuangmi.camera.079ae2 。這是控制候選，不是目前韌體待機實測證據。
- MISS fork 的 motor 介面沒有上述 MIoT 開關。2026-10-11 新增獨立 MIoT helper 接線，
  不替換 go2rtc；`standby` 使用原生 `on=false`，`show` 恢復 `on=true` 並讀回。
  原生開關不是切斷電源，也不是指定 PTZ 位置；UI 名稱為「隱私待機」。

## 操作 SOP

1. 先接入單一攝影機，確認已使用具備 MISS PTZ API 的實驗版與明確啟用旗標。
2. 即時畫面方向鍵與停止沿用原有 authenticated/CSRF Dashboard API，不新增 producer。
3. 選擇指定位置模式；移動到避開私人區域的位置，親自確認畫面並保存。
4. 手動回到正常監控角度後才能 hide。未保存正常原位置時不接受可逆隱私成功。
5. hide 先原子保存原位置，再送一次 position，使用 refresh 讀回確認。
6. show 使用已保存的原位置，驗證讀回再清除恢復記錄。記錄跨 Host 重啟保留。
7. 選擇隱私待機並保存；hide 先確認原生開關為 bool，再保存恢復記錄後關閉。
   show 必須有此裝置的恢復記錄才開啟，所有成功均要求屬性 code=0 與 bool 讀回一致。
   不支援、Session 無效、讀回失敗時安全拒絕；不以停止 AI 或串流逾時冒充隱私。

## 實作與邊界

- Dashboard 與 Telegram 共用同一個 PrivacyService／MissPrivacyAdapter。
- 設定在既有 Xiaomi state 目錄的 `xiaomi-private-pose.json`，僅含 device hash、模式、
  使用者指定位置與恢復位置；不含 DID、帳號、Token 或來源 URL。
- 切換攝影機後不能使用前一台的位置；恢復尚未完成時不能重新保存或改模式。
- 位置讀取失敗、未知狀態、指令接受但位置未改變，均不回報成功。
- 手動方向變更使隱私位置不符時顯示未知；show 仍可嘗試已保存的恢復位置。
- MISS MOTOR_RESP 無 request ID 的上游限制仍存在；位置讀回不是影像隱私的視覺證明。
- 正式 go2rtc binary、SYSTEM 安裝與登入 Session 未修改。
- `/status` 會讀回位置狀態；未讀回、模式未配置或待機未接通時不顯示已隱私／已恢復。

## 驗收

### 2026-10-11 原生 MIoT 開關實機驗收

- C701 `chuangmi.camera.079ae2`、`chuangmi.camera.026c02`，SG：
  同一份現存 go2rtc Session，fresh device list 確認 DID/model，官方 MIoT spec 動態找到
  `camera-control/on` 可讀寫 bool。本次兩台皆為 siid=2、piid=1，不將此值套用未知機型。
- 兩台均通過 `true → false → true`；每次 property code=0，最後恢復開啟。
- 026c02 恢復後透過原有 localhost RTSP stream（共用既有 Xiaomi producer），
  FFmpeg 退出碼 0，再取得有效 1920×1080 JPEG。C701 恢復後影格本輪未驗證。
- 證據：`reports/miot-on/c701-cycle.json`、`reports/miot-on/026c02-cycle.json`，不含秘密。
- 這是原生屬性讀回驗收，不等同實體遮罩、SD 卡錄影停止或新版正式 Dashboard 點擊驗收。
- SYSTEM config 直接讀取曾被權限拒絕；使用者 config 沒有對應 Session。
  正確做法是讀取目前 localhost go2rtc `/api/config`，僅在記憶體／helper stdin 使用，
  不保存或顯示內容，不重建空設定、不重新索取帳密、不移動 Session。
- helper 每次驗證 device identity 與 spec，只有 `camera-control/on` 有 read/write bool 才送出。
  與小米來源或 Tailscale 影音中繼分離：雲端開關不需要穿透攝影機 LAN，也不新增影音 producer。
- 單一 helper 操作鎖；唯讀狀態快取最多 15 秒，明確寫入不走快取、不自動重送。
- 運行 binary 以 SHA-256 校驗，stdin 傳送 Session，禁止 argv、stderr 或 raw response 洩漏。
- 建置：在現有 Go module `reports/ptz-feedback-fix/source` 下以既有 Go1.25.6 執行
  `go build -o <repository>/vendor/go2rtc/miot/xiaomi-miot.exe <repository>/tools/xiaomi_miot_helper.go`；
  build cache/module cache 放 repository reports 下。重新建置後必須更新 manifest hash。
- 本輪完整 495 tests（101.946 秒）、Ruff、compileall、即時畫面 JS 語法通過。
  新版正式 Host 尚未安裝；前端點擊、Telegram hide/show、關閉時錄影中斷與恢復影格
  尚需發布包安裝後端到端驗收。不能把原生屬性讀回當作以上全部完成。

下列為較早版本的歷史測試紀錄，不代表本次實機開關尚未測試：

`tests.test_miss_privacy` 驗證可逆位置、冪等、重新建立 Adapter 後恢復、不同裝置拒絕、
斷線拒絕、待機不冒充成功、設定保護及 Dashboard auth/CSRF/視覺確認。
測試使用模擬位置，真實攝影機 hide/show、C701 standby 與 Telegram message_id 均待驗收。
repository 已超前 r39，不能宣稱正式安裝版已有此功能。

本輪驗證：完整 334 tests（75.755 秒）通過；新增 status 隱私讀回案例後另執行最新全套，
最終結果以本輪回報為準。相關 56 tests（18.501 秒）及 Ruff 已通過。

最終版本驗證：335 tests（73.507 秒）全部通過，完整 Ruff 與 compileall 通過。
不包含真實位置移動或 C701 關閉／待機驗收。
