# MVP Phase 3C.2：Xiaomi 真實裝置同步報告

## 結論

MeowKeeper+ 已接上官方 go2rtc v1.9.14 的真實 Xiaomi discovery contract。歷史實機驗證中，Host
曾以既有登入 session 做唯讀 reconciliation；Dashboard 的重新載入會透過受保護的 POST
refresh，讀取 go2rtc 的已登入帳號與裝置來源，並以不含 DID、MAC、完整 IP 或完整
`xiaomi://` URL 的 opaque `internal_id` 顯示裝置卡片。

目前本機沒有可確認的登入 session：Phase 3C.4 早期 config port 判斷 bug 曾把 AppData
session config 替換成空的 localhost config。此狀態與歷史兩台裝置證據分開記錄；在官方
session 被外部恢復前，不得宣稱目前 Dashboard 已能同步真實裝置。

本次尚未宣稱 Xiaomi 影音已連線。兩台實機都在 profile probe 階段收到 go2rtc：

```text
go2rtc source allocation: no p2pid allocated
```

因此沒有猜測解析度、FPS、音訊或 JPEG，也沒有建立空的永久 stream。這是來源端／裝置狀態的可恢復錯誤，不是密碼錯誤；MeowKeeper+ 不會自動重試或清除現有登入 session。

本次真實同步另外確認：原有 AppData setup state 在目前受限 Windows runtime 下不可寫時，refresh 不再回 HTTP 500；服務會把不含秘密的公開狀態降級保存至 repository runtime state，且不會修改 go2rtc 的登入設定。用 session 的一次性可寫隔離設定測試 Standard 2K，仍收到相同的 `no p2pid allocated`，因此沒有可用 producer，也沒有第一張 JPEG。

## 已實作

- `Go2RtcClient`：localhost-only、受限 query、Xiaomi account/source discovery、controlled stream PUT/DELETE/status/probe；請求 URL 不寫入 log。
- `Go2RtcXiaomiDiscovery`：使用官方 WebUI 同樣的 `GET /api/xiaomi` 與 `GET /api/xiaomi?id=<account>&region=cn` contract；完整 source 僅在記憶體保留。
- `XiaomiSetupService`：刷新時建立安全裝置 schema；以 opaque internal ID 對應 private source；保留舊 device ID 相容解析；profile probe 失敗時保存安全錯誤。
- `XiaomiSetupService`：AppData 狀態檔不可寫時只將公開狀態降級保存至 repository runtime state，不觸碰 go2rtc session；Host 啟動會重新同步既有帳號，而不是再次登入。
- 受控串流名稱固定為 `xiaomi_camera_<index>`；只有 stable profile 通過後才建立並切入既有單一 `RepositoryCoreController`／`FfmpegMediaIngest`。
- profile probe 使用臨時 localhost go2rtc stream、FFprobe 與清理；不會留下無 producer 的永久 stream。
- 遇到 `p2pid` 缺失時，probe 在第一個 profile 即停止變體嘗試，剩餘 profile 標為略過，避免對 Xiaomi 上游快速重試。
- profile 名稱不再重複附加；單次 probe 只送出一組受控 profile，若建立暫時 stream 失敗會安全回傳 `degraded`，不會讓 Dashboard API 變成未處理的 HTTP 500。
- Dashboard：refresh loading/disabled state、裝置卡片、測試畫質、接入按鈕、單一來源／JPEG 狀態提示；DOM 使用 textContent，避免直接插入未信任裝置欄位。
- 外部管理的官方 go2rtc 程序現在可被安全辨識為 localhost healthy，但 MeowKeeper+ 不會宣稱擁有其 PID 或在停止動作中誤殺它。
- 歷史外部 go2rtc session 的 `prepare()` smoke：結果為 `account_ready`、2 個裝置，且
  隔離測試中的 config SHA-256 前後一致；目前 session 狀態以 Phase 3C.4 report 為準。

## 真實驗證

| 項目 | 結果 |
|---|---|
| 官方 go2rtc 版本 | `v1.9.14`，localhost API/RTSP 健康 |
| 已登入帳號 | 歷史 session 已確認；目前本機 session `not_checked` |
| 裝置 discovery | 歷史驗證取得兩個真實裝置：`isa.camera.hlc7` 與 `chuangmi.camera.026c02` |
| Dashboard refresh endpoint | 已接線；未登入的直接 HTTP probe 正確回 `401`；應用層 runtime direct refresh 通過 |
| Dashboard setup page／device container | HTTP 200；頁面含重新載入按鈕、裝置容器與 refresh endpoint |
| Standard 2K profile probe | API HTTP 200，但狀態為 `degraded`；go2rtc 回報 `no p2pid allocated`，9 個受控 profile 變體未建立 producer |
| controlled stream | 尚未建立，避免留下無 producer 的 stream |
| FFprobe／第一張 JPEG | `not_checked`；來源 producer 未能建立 |
| 單一 MeowKeeper+ ingest | 尚未啟動 Xiaomi source；目前不增加攝影機連線 |
| secrets | 報告、API 公開 schema、log 均未保存帳號、密碼、Token、DID、MAC、完整 IP 或完整 source URL |

## 目前狀態與下一步

歷史上 Xiaomi 帳號及兩台裝置已由 go2rtc session 確認；但目前 session 不可用，故不能
在不改變外部狀態的前提下重做 real-device sync。完成一次使用者在官方 go2rtc WebUI 的
明確 session 恢復後，才可在 Dashboard 按「重新載入裝置」→ Standard 2K「測試畫質」；
MeowKeeper+ 不會自動提交帳密、不會要求貼出 `xiaomi://`，也不會反覆重試。

若 profile probe 通過，下一步才會以 `xiaomi_camera_1` 建立受控 go2rtc stream，接入既有唯一 ingest，並以 `/api/v1/preview/jpeg` 驗證第一張真實 JPEG。

## 證據檔案

遮罩 JSON 與安全摘要位於 [`reports/phase3c2`](../reports/phase3c2/)：

- `reload-request.json` / `reload-response.json`
- `xiaomi-profile-probe.json`
- `stream-status.json` / `ffprobe.json`
- `host.log` / `go2rtc.log`
- `runtime-sync-evidence.json`：本次實際 Host／Dashboard／隔離 session 摘要；不含秘密。
- `go2rtc-session-redacted.log`：官方 v1.9.14 隔離程序的安全遮罩啟動摘要。

`xiaomi-devices.png` 與 `xiaomi-live.png` 尚未產生，因為本次沒有誠實可截圖的登入後 Dashboard session／有效 producer JPEG；不得以空白或 Mock 圖片充數。

## 回歸驗證

- `python -m compileall -q src tools tests`：通過
- `python -m ruff check src tools tests`：通過
- Xiaomi/go2rtc 與 Phase 3 Web/Tray 相關 unittest：通過
- 新增 `tests/test_xiaomi_phase3c2.py`：通過
- 新增狀態檔不可寫 fallback 測試；通過

完整家庭環境驗收仍需 camera p2pid 恢復後的 profile、FFprobe、單一 ingest、第一張 JPEG 與實際 Dashboard 登入 session；在此之前不能宣稱 Xiaomi 來源已完成或可長時間運行。
