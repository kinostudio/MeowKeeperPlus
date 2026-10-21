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

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。
