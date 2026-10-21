# 共用按需 H.264 相容預覽

2026-10-18，repository 實作與隔離驗收；未建置、發布、安裝或修改正式 SYSTEM Host。

## 已接線

播放順序為原碼 WebRTC → 原碼 MSE → 共用 H.264 MSE → JPEG。只有原碼播放失敗後才要求相容轉碼；不改攝影機畫質、AI 尺寸或 Telegram／環形快取的事件時間連續性。

- 第一位相容觀看者註冊暫時串流；同來源／epoch 的其他觀看者共用同一轉碼 producer。最多仍受四位連續觀看者上限保護；切換來源會關閉舊觀看者，不把舊轉碼接給新來源。
- Host 啟動的 FFmpeg **只讀本機 go2rtc RTSP**，沒有小米 URL、帳密或第二條攝影機直連。輸出 MPEG-TS 經隨機路徑、loopback-only HTTP 端點交給 go2rtc，再由既有登入／同源 WebSocket 提供給瀏覽器。
- 不繞過 go2rtc 的「禁止透過 API 加入 exec」保護，不修改 go2rtc 安全設定。HTTP 僅允許 loopback、正確隨機路徑及單一 consumer；使用 OS socket 背壓、不累積影片到記憶體或磁碟。
- 最後觀看者離開、休眠、關閉頁面、session 撤銷或 Host 關閉會釋放轉碼、關閉暫時 HTTP 端點及移除本輪暫時 stream。只停止手上持有的 FFmpeg process handle，不按名稱批次終止程序。
- 硬體編碼器以真實初始化探測依序試 `h264_qsv`／`h264_nvenc`，不是看 GPU 名稱或 encoder 清單。初始化失敗採 CPU `libx264`。探測最多各三秒，結果在同一 Host 快取。
- 硬體路徑最高 1920×1080／20 FPS；CPU 備援最高 1280×720／15 FPS、ultrafast、兩條解碼／編碼工作執行緒。小來源不補成高解析度，保持比例及 yuv420p，不傳音訊。這些是上限，不是實際速度或低於 40% 負載保證。
- 硬體初始化通過但真實來源編碼失敗時，本版仍安全退回 JPEG；尚未實作同次觀看中的硬體失敗→CPU 熱切換。未重複無限重試。HUD 標記 H.264 相容轉碼與實際尺寸／FPS，CPU 上限就地說明。

## 驗收結果

| 項目 | 結果 |
| --- | --- |
| 無觀看者不探測／不轉碼、雙 acquire 共用、最後 release 清理、來源 epoch 不混用 | passed：`tests.test_preview_transcode.TranscodeTests` |
| 既有登入／同源保護、轉碼 lease 釋放、固定本機指令、硬體探測失敗 CPU 備援 | passed：同上及 `tests.test_preview_mse` |
| 真實 HEVC→H.264、兩位 WS consumer、晚加入、有效 MP4 可解 JPEG、單一原始 producer／單一轉碼 producer、清理 | passed：`tests.test_preview_transcode.RealTranscodeTests`；合成 320×180 HEVC、真實 vendor FFmpeg／go2rtc，不是攝影機實拍 |
| 真實 Edge 自動回退 | passed：明確關閉測試瀏覽器 HEVC 支援與 WebRTC，透過已登入 Dashboard WS，原碼 MSE 失敗後自動 H.264；5 秒呈現約 12.39 FPS（CPU 路徑上限 15）、320×180，頁面關閉後 viewers 歸零。`reports/camera-ui-redesign-2026-10-18/h264-browser.json` |
| 完整回歸 | passed：876 tests／254.522 秒／OK；當時新增媒體測試啟用、無略過。後續瀏覽器測試支線及 JS 回退循環另重驗通過，不冒充當時已包含新支線 |
| 啟動碼 | passed：Windows PowerShell 直接執行 `scripts/Start-DevelopmentHost.ps1 -NoOpenBrowser`，18981 `/healthz` HTTP 200、監控停止。未安裝／提權 |

另外本機短測初始化選到 `h264_qsv`；只證明當下該機可初始化，不是 MX250、真實 4K→1080p／20 FPS、功耗或長時間穩定證據。

本輪曾抓到 FFmpeg RTSP 不接受 `-rw_timeout`，依實際 stderr 改為 RTSP `-timeout`。錯誤輸出只在 producer 保留有界 2 KB 記憶體摘要，不直接公開給瀏覽器；WebSocket 錯誤不洩漏路徑或 source。

## 如何啟動最新開發 Host

一般 PowerShell 執行，不需要管理員；重用健康的相同埠 Host，不自動重啟：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\scripts\Start-DevelopmentHost.ps1"
```

網址 `http://127.0.0.1:18981/dashboard`。資料獨立放在專案 `data/development-host`，首次設定帳號及來源後才能驗真實攝影機；不匯入或混用正式資料。啟動時不自動監控、只綁 localhost；沒有建立防火牆規則或開放 go2rtc API。

先前由本任務啟動的 18980 隔離 Host 已按 PID、完整路徑、命令列及建立時間核對後關閉；正式服務未動。新的 18981 開發 Host 保留供使用者操作。

## 仍未驗證／未承諾

真實 C701 4K HEVC→1080p20、MX250／Intel 筆電長期負載與溫度、不同瀏覽器與異地 Tailscale、正式包及長時測試。HEVC 原碼 WebRTC仍未實作；本輪相容轉碼走 MSE，不是 HEVC WebRTC 支援宣告。
