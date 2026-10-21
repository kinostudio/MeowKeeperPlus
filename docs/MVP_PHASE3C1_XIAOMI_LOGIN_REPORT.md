# MVP Phase 3C.1：Xiaomi go2rtc 70016 登入診斷報告

更新日期：2026-08-02（Asia/Taipei）

## 範圍與結論

本階段只處理 Xiaomi 帳號登入回應的安全擷取、分類、冷卻與可恢復狀態。
沒有修改畫質、RTSP、YOLO、聲音或裝置 profile 流程，也沒有要求使用者提供密碼、驗證碼或
Token。

目前提供的實際回應形狀：

```json
{"code":70016,"description":"登入驗證失敗","captchaUrl":null,"securityStatus":0,"result":"error"}
```

被分類為 `upstream_api_changed`，不是 `invalid_credentials`。因為 Xiaomi 沒有提供
captcha URL、驗證挑戰或明確的帳密錯誤訊息，不能從 70016 推導密碼錯誤。Dashboard 顯示：

> 小米伺服器拒絕此密碼登入流程；帳密可能正確，但目前 go2rtc 登入方式可能受到驗證或 API
> 相容性限制。

## 官方 go2rtc 比對

- 核准的正式版本仍是 [go2rtc v1.9.14 release](https://github.com/AlexxIT/go2rtc/releases/tag/v1.9.14)。
- [官方 release 清單](https://github.com/AlexxIT/go2rtc/releases) 沒有顯示比 v1.9.14 更新的正式版；本次沒有下載
  nightly、master、第三方 fork 或鏡像，也沒有修改 `vendor/go2rtc` manifest。
- v1.9.14 的 [官方 Xiaomi cloud.go](https://raw.githubusercontent.com/AlexxIT/go2rtc/v1.9.14/pkg/xiaomi/cloud.go)
  將 CAPTCHA 與手機／Email verification 暴露為 `LoginError` 的 presence；它沒有提供
  70016 的 typed classification。
- [官方 issue #1985](https://github.com/AlexxIT/go2rtc/issues/1985) 已關閉並標記 v1.9.14，內容是
  captcha／驗證碼流程修正；這不能證明沒有 challenge 詳細資料的 70016 已完全修正。
- 目前可見的 [issue #2026](https://github.com/AlexxIT/go2rtc/issues/2026)、
  [issue #2030](https://github.com/AlexxIT/go2rtc/issues/2030) 與 [issue #2029](https://github.com/AlexxIT/go2rtc/issues/2029)
  是串流協定／連線 keepalive 議題，不是 70016 帳號登入修正；不能拿來當成登入成功證據。

因此本階段沿用已驗證的 v1.9.14，保留現有 rollback 版本，不以未發布程式替代正式版。

## 安全擷取

`Go2RtcClient.get_diagnostic("/api/xiaomi")` 與 Dashboard 的「擷取安全登入診斷」會取得：

- HTTP status
- Content-Type
- JSON 或文字 response body 的安全遮罩摘要（最多 4096 字元）
- go2rtc 合併 stdout/stderr 日誌的有限尾端（最多 4000 字元）
- transport error

`XiaomiLoginDiagnostic` 只保留 `code`、截短且遮罩的 description、challenge presence、
security status 訊號與安全 `LoginError` 結構。密碼、帳號、Token、cookie、verification code、
captcha 內容、完整 URL 及 Xiaomi location URL 不會寫入 state、API 或日誌。

登入請求與驗證是兩個不同階段。若確實需要由受控呼叫端送出一次官方
go2rtc form POST，可使用 `Go2RtcClient.post_xiaomi_login()`；欄位只放在
HTTP body，方法不自動重試、不保存、不記錄秘密。POST 收到 HTTP 200 空 body
只代表「登入請求已接受」，接著必須以獨立 GET `/api/xiaomi` 驗證帳號清單。

目前使用者提供的回應沒有同時提供 HTTP status、Content-Type 或 go2rtc stderr，因此這三項在
實際本機診斷前標示 `not_checked`；程式已可在 localhost go2rtc 執行時擷取，且不會自動重試。

## 分類契約

| 分類 | 判斷原則 |
|---|---|
| `invalid_credentials` | 只有明確帳密錯誤文字／契約訊息；裸 401 不算 |
| `verification_required` | `need_verify`、notification URL、手機／Email 驗證或明確驗證挑戰 |
| `captcha_required` | CAPTCHA URL／圖片或明確 CAPTCHA 訊息存在 |
| `xiaomi_risk_control` | security status 非零或明確風控／鎖定訊息 |
| `unsupported_login_flow` | 裸 401、瀏覽器／QR／Token 流程不支援提示 |
| `upstream_api_changed` | 70016 無 challenge 細節、錯誤結果缺少 go2rtc 需要的登入欄位、5xx |
| `network_error` | localhost client timeout、DNS、連線或 TLS transport error |
| `unknown_error` | 以上訊號皆不存在 |

flow-aware 分類器 `classify_login_flow_response()` 的額外規則如下：

- `phase="post"` 且 HTTP 200、空 body：`login_success`（登入請求已接受，尚未完成帳號驗證）。
- `phase="get"` 且 HTTP 200、帳號清單包含本次新 UID：`login_success`。
- `phase="get"` 且 HTTP 200、未包含本次新 UID：`login_failed`。
- flow phase 的 HTTP 500 或非預期錯誤：`login_failed`。
- HTTP 401 且存在 CAPTCHA／手機／Email 驗證訊號時，仍優先分類為對應 challenge，
  不會被 `login_failed` 覆蓋。

帳號 UID 只在記憶體內比較；診斷只保存帳號數量及 UID 是否存在，不保存 UID 原值。
HTTP 200 帳號陣列不再落入 `unknown_error`。

特別規則：`70016 + captchaUrl=null + securityStatus=0` 固定走
`upstream_api_changed`，不得直接顯示「密碼錯誤」。

## 可恢復登入流程

1. 登入診斷紀錄後進入 `login_blocked`、`captcha_required`、`verification_required` 或
   `degraded`，並保存分類與安全 evidence。
2. 預設登入冷卻為 300 秒；冷卻期間 `open-login` 與 `refresh` 不會自動提交或重試。
3. Dashboard 顯示剩餘秒數；使用者按「明確重試登入」才清除 cooldown gate。
4. 清除 gate 只回到 `waiting_for_login`，仍須由使用者在官方本機頁面完成登入／驗證；程式不
   接收密碼、CAPTCHA、驗證碼或 Token。
5. 登入成功後才可由既有 `refresh` 進入 `loading_devices`；本機後續已觀察到官方 go2rtc
   session 可列出裝置，但 MeowKeeper+ 仍不讀取或保存帳號 token。

## 本次驗證與實機狀態

| 項目 | 狀態 |
|---|---|
| 官方 Xiaomi 帳號登入已由使用者確認 | 已由 go2rtc v1.9.14 現存 session 間接確認；MeowKeeper+ 未重新登入 |
| 提供的 70016 回應分類 | 已由 fixture／單元測試驗證：`upstream_api_changed` |
| 70016 診斷 HTTP status／Content-Type | 既有 fixture 仍為 `not_checked`；實際 discovery API 為 HTTP 200／application/json |
| go2rtc stderr／合併日誌 | `not_checked`；程式只取安全遮罩尾端 |
| Xiaomi LoginError structure | 已實作 presence-only safe shape；真實 response 尚未提供 |
| 官方正式版修正 | 沒有比 v1.9.14 更新的正式版；v1.9.14 已含 issue #1985 captcha fix |
| 使用的登入路線 | 官方 go2rtc localhost browser flow；不使用 token extractor |
| Xiaomi account token | 未讀取、未保存；僅使用 go2rtc localhost session |
| load devices | 已進入並列出兩個真實裝置；profile／JPEG 另見 Phase 3C.2 報告 |

目前登入流程沒有要求使用者再次貼出任何秘密。若要完成串流驗收，請依
`docs/MVP_PHASE3C2_XIAOMI_DEVICE_SYNC_REPORT.md` 的唯一操作恢復攝影機 p2pid，再按
Dashboard 的 profile probe；不要把任何密碼、驗證碼或 Token 貼給 MeowKeeper+。

本報告不宣稱 Xiaomi 帳號登入、裝置載入或家庭攝影機串流已完成。
