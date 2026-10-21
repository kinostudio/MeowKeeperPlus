# r107 安裝版 C701 無影格：遠端實機診斷

2026-10-19。經已連線的 VS Code Remote-SSH 終端實際執行；不公開帳號、私人裝置識別或來源 URL。

## 實機證據

- `passed`：正在執行的 Host、go2rtc 均為一般使用者；Core 排程的 SYSTEM principal 不能替代實際 owner 證據。
- `passed`：go2rtc 實際載入該使用者 LocalAppData 的設定；既有小米帳號與 SG 裝置清單各有一筆符合目標。
- `failed`：現有 subtype=3 串流的 producer 只有 URL 宣告、沒有媒體或接收位元組；一個既有下游等待，Host 無有效影格、AI FPS=0、快取為空。
- `failed`：本輪最新日誌持續出現 `xiaomi: probe: miss: read media: cs2: read tcp ... i/o timeout`。
- `failed`：同一串流強制 UDP 仍出現媒體讀取逾時；已還原原本未指定 transport 的完整來源。未新增攝影機 producer。
- `passed`：唯讀 MIoT helper（已核對內附 manifest）回傳 `success=true`、`stage=verified_property`、`siid=2`、`piid=1`、`property_code=0`、`on=false`。已確認攝影機原生開關為關閉，能解釋有區網／控制連線卻無影格。
- `passed`：使用者明確同意解除待機後，原生開關寫入及另建 client 的獨立查詢均回傳 `on=true`、`property_code=0`、`success=true`。
- `passed`：同一已存在 go2rtc stream 經內附 FFmpeg 解碼產生有效 JPEG，569,462 bytes、3840×2160，FFmpeg exit=0。
- `passed`：同一 stream 連續取流60.087秒、847個影格封包、FFmpeg exit=0、stderr為空、正常 `progress=end`；牆鐘60.32秒。開始／結束 producer各1個，原本 Host consumer仍保留，接收位元組從5,959,353增加到8,301,252，實際協定為 `cs2+tcp`。平均約14.1個影格封包／媒體秒，不宣稱已達20 FPS。
- `passed`：真實遠端 Dashboard 重整後載入1920×1080 JPEG，DOM `complete=true`、`naturalWidth=1920`、`naturalHeight=1080`，顯示已接收最新影格、AI正在分析，快取持續累積。
- `passed`：正式即時頁原碼 MSE 播放，瀏覽器實際 `videoWidth=3840`、`videoHeight=2160`、`readyState=4`、`paused=false`、`currentTime=22.514276`（觀測時間UTC 2026-10-18 21:14:48）。不將來源規格20 FPS當成瀏覽器實際達成幀率。
- `passed`：第二次 MSE 觀測為UTC 21:15:27、播放時間62.179096秒、`readyState=4`，畫面持續前進。正式 Host 快照UTC 21:13:19至21:14:53約94秒，FFmpeg restart_count均為383；歷史錯誤文字與累積計數不代表這94秒又發生中斷。最後快照新影格距檢查約0.112秒、單一來源連線、42段影音快取，ONNX Runtime CUDA仍有分析結果。

這些證據未證明「安裝版使攝影機關閉」，也未確認先前是由哪個介面或操作關閉。

## 程式修正

小米「檢查連線／防火牆」加入最多20秒的原生開關唯讀查詢。只有成功回傳布林狀態才標示開啟／待機；查詢失敗維持 unknown。確認待機時明確提供原因及米家 App／已設定待機恢復操作的下一步，不送出開關命令。既有防火牆、單張及持續串流驗收結果獨立保留。前端此項請求等待上限75秒，涵蓋 Windows 診斷45秒加原生查詢20秒。

故障回歸 ID：`XIAOMI-STANDBY-NO-FRAMES`。此原始碼修正尚未建置、安裝或覆寫正式 Host。

安全摘要：[實機驗收JSON](../reports/c701-r107-recovery/acceptance-2026-10-19.json)。原始影格只保存在遠端私人暫存，不納入repository或發布包。

## 隔離驗收

- `passed`：`tests.test_miot_power`、`tests.test_xiaomi_firewall_compat` 共11項，包含布林關閉狀態、未知狀態、唯讀 stdin、不送出 on 寫入、逾時、秘密遮罩及原有防火牆測試。
- `passed`：修改範圍 Ruff 與 Python compileall。
- `not_checked`：新診斷介面在新發布包／正式 Host 的瀏覽器結果；未覆寫已安裝程式。
