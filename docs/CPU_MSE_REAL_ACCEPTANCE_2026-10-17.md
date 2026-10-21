# CPU 持續監測與真實攝影機 MSE 驗收（2026-10-17）

## 結果範圍

| 項目 | 結果 | 實際證據 |
| --- | --- | --- |
| 正式 r104 的 MSE | failed | 實機按流暢立即退回節能；其內附 Python 的 websockets_available=false |
| 修正後 repository 的真實 MSE | passed（隔離） | 同一攝影機 go2rtc producer，Edge 解碼 1920×1080，HUD 約 20 FPS，播放超過 82 秒 |
| 兩個觀看頁共用上游 | passed | producer=1；原本 RTSP consumer=1，加兩頁後 MSE consumer=2、總 consumer=3 |
| 節能／休眠／流暢切換 | passed（隔離） | JPEG 天然尺寸 1920×1080；休眠 img/video hidden、src=null、video paused；重新流暢至 17.88809 秒、20.3 FPS |
| 關閉觀看頁後核心監控 | passed | producer=1、consumer=1（rtsp）、MSE consumer=0；共享 JPEG 年齡 0.09 秒，正式 healthz=200 |
| CPU 持續實溫 | passed（管理員隔離3分鐘） | 36／36有效取樣，78～95°C、平均86.03°C，最大間隔5.161377秒，helper／monitor正常退出；一般權限仍不可讀 |

不安裝、不提權、不重啟或修改正式 Host；未更改攝影機畫質、PTZ 或 go2rtc 設定。

## MSE 方法與數據

以 repository 現有 `reports/r105_preview_acceptance.py --port 18954 --seconds 600` 啟動 loopback-only 實際 app/templates/JS。臨時帳號、臨時資料目錄，不讀正式 Session。此 harness 只讀既有共享 JPEG，選取唯一已有 consumer 的攝影機串流，沒有建立第二個攝影機 ingest。

三筆第一觀看頁 DOM 數據：

| 觀測相隔 | currentTime | 實際尺寸 | readyState | paused | HUD |
| --- | --- | --- | --- | --- | --- |
| 起點 | 11.189952 | 1920×1080 | 4 | false | 20.0 FPS |
| 約 31.955 秒後 | 43.107269 | 1920×1080 | 4 | false | 20.0 FPS |
| 再約 39.894 秒後 | 82.477232 | 1920×1080 | 4 | false | 20.3 FPS |

MSE consumer 的 go2rtc format_name 為 mse/fmp4，並非把 JPEG 更新標示成影片。前端先試 WebRTC 後落到 MSE，這次實測不是 WebRTC 成功。自動化 DOM 介面未提供掉幀計數，因此未量測掉幀率／端到端延遲／瀏覽器解碼負載，不由來源 20 FPS 推論這些數據。

證據（私人攝影機畫面只留本機，不公開發布）：

- `reports/mse-real-playback-2026-10-17.json`
- `reports/mse-real-1080p-20fps-2026-10-17.jpg`
- `reports/mse-r104-fallback-2026-10-17.jpg`

驗收結束已關閉兩個測試頁與隔離 server，保留使用者原本正式分頁。正式版仍為 r104；不能把隔離成功說成正式版已修復或部署。

## CPU 權限與下一步

本次核對執行邊界：預設 aero15\codexsandboxonline；經授權的唯讀測試身分 aero15\aaron。後者直接啟動已校驗的 CPU helper，同樣回 permission_required。沒有因此停用防護、調整驅動 ACL、建立 SYSTEM 工作、觸發 UAC 或沿用先前溫度快照。

已準備 `reports/cpu_continuous_acceptance.py`：預設 180 秒、每 5 秒同一 helper 取樣，記錄時間間隔、溫度、helper CPU/RSS、退出狀態；權限不足即中止，不連續重試同一失敗。通過要求為完成期間、有效樣本至少預期 90%、最大間隔不超過 15 秒、結束後 helper 與背景工作都停止。這是短期持續測試，不是 24H 驗收。

使用者另行同意並由管理員終端執行，不自動提升整個 Host。結果寫入 `reports/cpu-continuous-real-result.json`。2026-10-17 17:50:18～17:53:18 GMT+8，180.109秒、36／36有效讀值、最低78°C／最高95°C／平均86.0278°C，最大間隔5.161377秒，helper與monitor退出通過。Helper峰值RSS 29,908,992 bytes；穩態CPU計時增量為0，受計時粒度限制，不代表零成本或整體Host低負載保證。這是本機i7-10875H短測，不是MX250筆電、24H或正式部署驗收。
