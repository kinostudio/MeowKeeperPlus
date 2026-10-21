# WebRTC、Telegram回報與來源尺寸記憶（2026-10-17）

本次為repository修改，沒有建置、安裝、變更正式Host或防火牆。

## 已接線

1. 流暢模式依序嘗試WebRTC → MSE → JPEG。WebRTC目前僅支援H.264原碼，使用Dashboard自己的aiortc媒體peer；不是開放go2rtc WebRTC/API。唯讀、同源、既有Session驗證、最多4個影片觀看槽；每秒檢查Session與來源，登出撤銷實際SRTP媒體。沒有STUN/TURN、沒有自動開防火牆。
2. 全部WebRTC觀看者共用一個本機go2rtc RTSP封包讀取器；decode=False，不新增FFmpeg或攝影機上游。佇列有界，最後一位離開釋放讀取器；休眠／隱藏頁面停止該頁播放，不停止核心監控。
3. Telegram新提交的已登錄寵物逐軌跡身分回報預設可用於本機特徵學習，移除額外同意／撤回按鈕和學習提示。草稿、未知／其他貓／不是貓不自動變成已知身分樣本；歷史同意欄位不批次改寫。品質、去重與衝突保護仍保留，不等於自動重訓YOLO。整體「兩隻都在」不能代替每條track的身分配對。
4. 每台攝影機保存已使用profile的width／height／fps／codec／驗證時間；重新開啟顯示「上次驗證」，使用已保存來源接入時自動驗證，不要求先按人工測試畫質。切換profile不沿用舊尺寸。歷史尺寸不是當下有效影格的證據；資料保存於目前Data root，跨可攜包仍需搬移／還原資料。

## 隔離證據

- 真實aiortc雙peer同時收到可解碼H.264影格，共用一次來源開啟；撤銷一個Session後該peer關閉，另一個繼續；全部離開後viewers=0、來源關閉、槽位歸零。
- Edge實際網頁以合成H.264影片播放，HUD讀到640×360、20.0 FPS；截圖在 `reports/rtc-h264-browser.jpg`。這不是攝影機／MX250效能證明。
- 關閉本次兩個測試分頁後，隔離metrics回報 `viewers=0, source_open=false`。
- 49項WebRTC／Telegram回報／來源偏好測試通過（7.541秒）；包含新確認回報落庫learning_consent=1及重建偏好物件後尺寸仍存在。
- 完整回歸803項／233.544秒／OK；最後的提示文字、型別與新增斷言另跑上述49項通過。
- Ruff、compileall、兩個預覽JS語法檢查通過；已知故障門檻11類21項通過、無skip。測試分頁已關閉，隔離服務已退出。
- .venv及prepared embedded runtime均可匯入aiortc 1.15.0／av 17.1.0，pip check通過；不等於已產生新發布包。

## 仍待實機驗收

- 本機／LAN／Tailscale的真實攝影機、手機瀏覽器與防火牆矩陣；H.264實際播放FPS、掉幀、延遲、CPU／GPU與兩人觀看上游數量。
- HEVC使用MSE備援，不宣稱已實作HEVC WebRTC或共用H.264轉碼。來源目前沿用既有local Xiaomi stream解析契約，不任意代理使用者提供的RTSP。
- 本輪未傳送真實Telegram回條，未重啟正式攝影機；這兩項標為not_checked。

## 避免再犯

aiortc H.264 codec preferences必須在setRemoteDescription之前設定。只看到ICE connected不代表收到可解碼畫面；若先協商成VP8再送H.264封包，會連線成功但黑畫面。測試必須實際recv解碼影格，不能只檢查peer狀態。版本固定且建包需檢查實際匯入，因共享清理使用pinned MediaPlayer生命週期介面。
