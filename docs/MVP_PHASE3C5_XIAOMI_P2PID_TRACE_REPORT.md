# MVP Phase 3C.5：Xiaomi p2pid trace isolation report

## 結論

隔離的官方 go2rtc v1.9.14 已恢復真實 Xiaomi Session，並在相同 China region、
單一 `subtype=sd` stream、每台一個受控開流請求下重現：

`HTTP 500 / text/plain; charset=utf-8 / no p2pid allocated, please reboot device`

兩個 model 的官方 trace 都在 producer 建立前顯示 `dial cons=0 prod=0`。目前沒有
第一個 Video Packet 或 JPEG，因此不得宣稱 Xiaomi 影像已解決。

## Session 與裝置

- go2rtc：官方 v1.9.14，正式 binary 未替換。
- 隔離 API：`127.0.0.1:21984`；隔離 RTSP：`127.0.0.1:28554`。
- 隔離設定與工作目錄仍位於 `reports/phase3c4/standalone-go2rtc/`；正式設定未使用。
- 登入後 `/api/xiaomi`：HTTP 200、`account_count=1`。
- 重啟同一隔離 binary 後仍為 `account_count=1`，證明 session 已持久化，不需第二次登入。
- `region=cn` 裝置數量為 2：`isa.camera.hlc7`、`chuangmi.camera.026c02`。
- MeowKeeper+ Host、Tray、正式 go2rtc 均未啟動；每台測試完成後隔離程序停止且 port 釋放。

## 受控測試

每台裝置只做一次：

1. `PUT /api/streams` 建立一個暫時 stream。
2. 僅使用 `subtype=sd`。
3. 由 `/api/streams?src=...&video=all&audio=all` 觸發官方 Xiaomi source open。
4. 讀取狀態數次，未啟動第二個 consumer。
5. 立即刪除暫時 stream，停止隔離 go2rtc。

| Model | PUT | Open | Content-Type | 安全錯誤 | trace dial | active producer peak | consumer peak | JPEG |
|---|---:|---:|---|---|---|---:|---:|---|
| `chuangmi.camera.026c02` | 200 | 500 | `text/plain; charset=utf-8` | `no p2pid allocated` | `cons=0 prod=0` | 0 | 0 | 否 |
| `isa.camera.hlc7` | 200 | 500 | `text/plain; charset=utf-8` | `no p2pid allocated` | `cons=0 prod=0` | 0 | 0 | 否 |

Response body SHA-256（兩台相同）：

`62e3dd3f16b3a95d4ef084c5dee016f25adac35ab0df03681953da4ae2a050bc`

## Trace 定位

官方 trace 的關鍵安全片段為：

```text
TRC [api] PUT /api/streams?...&src=<url redacted>
TRC [api] GET /api/streams?...&video=all&audio=all&microphone= <redacted>
TRC [streams] dial cons=0 prod=0 error="xiaomi: no p2pid allocated, please reboot device"
```

trace 沒有暴露 `vendor`、`p2p_id`、`public_key` 或 `sign`。這不是把它們寫入報告；
而是官方 v1.9.14 localhost API 沒有獨立的 `miss_get_vendor` 公開端點，該呼叫在
Xiaomi source open 內部完成。報告因此將：

- `p2p_id_observed_in_trace=false`
- `has_p2p_id=null`（官方回應未暴露，不能把「未觀察到」冒充完整 vendor response）
- `vendor_observation=not_exposed_or_not_observed`

同時保留明確的 allocation error 分類：

`XIAOMI_CLOUD_DID_NOT_ALLOCATE_P2PID`（當時依錯誤文字暫列；這不是 vendor／p2p_id 的直接證據。2026-09-20 起診斷工具對同類文字改列 `P2PID_ERROR_TEXT_ONLY_UNVERIFIED`，應以正確後端 region 與真實影格結果重新判斷。）

安全 trace 檔：

- `reports/phase3c5/chuangmi-trace.json`
- `reports/phase3c5/chuangmi-trace.trace.log`
- `reports/phase3c5/isa-trace.json`
- `reports/phase3c5/isa-trace.trace.log`

trace 已遮罩 URL、帳號參考、DID、MAC、Token、密碼及其他 provider identifiers。

## PR #2264 門檻

本輪**不符合** PR #2264 實驗條件。該實驗要求官方 v1.9.14 的 `miss_get_vendor`
結果明確為 `vendor=cs2` 且有 `p2p_id`，再加上 LAN CS2 路徑不可用。本輪 trace
沒有暴露 vendor 或 p2p_id，且錯誤發生在 `prod=0` 的 allocation 階段，因此：

- 沒有下載、套用或編譯 PR #2264。
- 沒有使用 nightly、master、fork、第三方 binary。
- 沒有修改正式 go2rtc、launcher、port bridge、Dashboard 或 profile 邏輯。

## 後續狀態

Xiaomi 串流在 MeowKeeper+ 中維持 `experimental / blocked`。目前唯一安全下一步是
等待官方上游明確修正或能提供可驗證的 `cs2 + p2p_id` 結果；不要再重啟攝影機、
反覆嘗試登入或進行 profile sweep。要重新測試時，必須再次使用隔離設定、單一
stream、單一 consumer，並在取得第一張真實 JPEG 前保持「未完成」標記。
