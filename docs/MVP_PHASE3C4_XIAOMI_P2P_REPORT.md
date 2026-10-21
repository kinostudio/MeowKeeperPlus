# MVP Phase 3C.4：Xiaomi P2P standalone isolation report

## Final classification

**UPSTREAM_SHARED_XIAOMI_P2P_FAILURE**

兩個不同 model 在官方 go2rtc v1.9.14 的獨立程序中，以相同 China region、相同
已驗證 session、單一 `subtype=sd` stream 測試，均在 producer 建立前得到相同的
`no p2pid allocated`。因此目前沒有證據指向 MeowKeeper+ Dashboard、CoreController、
FfmpegMediaIngest、profile probe 或重複 producer。

## 測試隔離

- 測試前已安全停止由 repository 命令列識別的 Host、Tray、launcher 與正式 go2rtc。
- `standalone-after-stop.json` 證明 MeowKeeper+ process count = 0、8000／1984／8554
  均無 owner。
- 使用官方 go2rtc v1.9.14、獨立 working directory、獨立 config、API `2984`、RTSP
  `9554`；沒有啟動 MeowKeeper+、FFprobe consumer 或第二條來源連線。
- session 由現有官方 go2rtc 設定安全複製至隔離目錄；原始設定沒有被覆寫。
- 測試結束後已刪除隔離目錄內的原始 session config，只保留 `config.redacted.yaml`；
  診斷報告不保存可用 token。
- 兩輪程序是先後執行、沒有同時存在：chuangmi 測試 PID `9952`，清理後再以 PID
  `5172` 測 isa；最終 `standalone-final-after-stop.json` 證明 process count = 0。
- MeowKeeper+ `source_activation_requests = 0`；standalone 只發出兩次直接 stream
  PUT（每台一次），沒有呼叫 MeowKeeper+ activation 或建立第二個 producer。

## 真實結果

| Model | Stream | PUT | 開流 HTTP | Content-Type | 錯誤 | Producer peak | Consumer peak | 第一張影格 |
|---|---|---:|---:|---|---|---:|---:|---|
| `chuangmi.camera.026c02` | `subtype=sd` | 200 | 500 | `text/plain; charset=utf-8` | `no p2pid allocated` | 0 | 0 | 否 |
| `isa.camera.hlc7` | `subtype=sd` | 200 | 500 | `text/plain; charset=utf-8` | `no p2pid allocated` | 0 | 0 | 否 |

兩個 stream 都在測試後以 HTTP 200 刪除；沒有進行 profile sweep。因沒有 producer，
無法取得 video codec、resolution 或第一張影格。

登入／裝置狀態方面：使用者已確認官方 Xiaomi 登入成功；隔離 API 讀取 China region
成功取得兩台實際裝置。這只證明 login/load devices，並不代表 P2P allocation 成功。

## 網路邊界

唯讀結果保存於 `reports/phase3c4/network-redacted.json`：

- Windows 10 Pro build 19045。
- 一張 active Wi-Fi 介面，IPv4 僅保留 `192.168.31.xxx/24`。
- Windows Firewall profile 狀態已讀取；未修改防火牆、路由、網卡、VPN 或防毒。
- standalone 清理後 API／RTSP／WebRTC 測試埠均已釋放。
- 為避免重新觸發攝影機或 P2P allocation，本次不再做新的 camera LAN、UDP socket 或
  profile probe；這些欄位保持 `not_checked`。

## 影響與處置

- MeowKeeper+ 串流核心：**不需要因本次 p2pid 結果修改**。
- Xiaomi go2rtc 路線：保留為 `experimental / blocked`；不再自動重試帳密、不再要求
  重啟攝影機、不再進行其他 profile sweep。
- 上游 issue 草稿：`reports/phase3c4/go2rtc-xiaomi-p2pid-issue-draft.md`。
- 安全 raw evidence：`reports/phase3c4/chuangmi-standalone.json`、
  `reports/phase3c4/isa-standalone.json`；內容沒有帳號、Token、DID、MAC、完整 LAN IP
  或完整 `xiaomi://` URL。

## 使用者下一個唯一操作

**暫停 Xiaomi 串流路線，等待官方 go2rtc／Xiaomi P2P allocation 的正式修正或明確
支援說明；不要再重啟攝影機或反覆嘗試登入。** 後續只有在官方正式版本確認修正後，
才重新啟用一次隔離測試。
