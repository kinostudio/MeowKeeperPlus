# C701 遠端中繼配對候選版（2026-10-06）

## 本輪已驗證

- 正式 Host r54 可攜包已建置並由 `tools/verify_portable.py` 完整校驗：19,065 檔、私人資料檢查通過，預設 YOLO11n SHA-256 為 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`。
- C701 中繼 r2 ZIP 已建置，官方 go2rtc v1.9.14 SHA-256 固定校驗，內附 FFmpeg、設定／停止／配對腳本；16 檔，manifest 逐檔核對通過。ZIP SHA-256：`F0A1BEB582D40CA8F539619B844D128B0D89C20FCBC9799748EA0D7A5390758A`。
- `tests.test_xiaomi_relay_pairing` 四項通過：過期／非 Tailnet 位址、相同裝置核對、秘密不落地、已保存位址遭修改時拒絕啟動。全部 370 項 unittest 通過；Ruff、Python compileall、Dashboard 配對頁 JavaScript 語法與 PowerShell Parser 通過。
- 遠端包中的新 PowerShell 腳本已加入 UTF-8 BOM；未將 Xiaomi Session、DID、Token、JPEG 或完整 `xiaomi://` 來源寫入包內。

## 接線方式

1. 攝影機所在 Windows 電腦執行獨立 C701 中繼，官方 go2rtc API 僅聽 `127.0.0.1:21984`；RTSP 僅聽該電腦 Tailscale IPv4 的 `:28554`。遠端電腦必須親自完成 Xiaomi 登入；正式 Host 不接受帳密或 Token。
2. 遠端先用同一輪 `sg` 裝置清單確認 `chuangmi.camera.079ae2`，並取得一張真實 JPEG；然後產生 10 分鐘有效的配對碼。配對碼包含裝置指紋，但不包含 DID、帳號或 Token。
3. 正式 Host 在已登入的 Dashboard 裝置卡片驗證配對碼與同一台裝置，再經 Tailscale 取得影格。通過後只保存 Tailnet IP、RTSP port、stream name、型號及 region；按「接入遠端監控」才切換唯一影音來源。
4. 正式 Host 重啟時由既有來源設定恢復監控；遠端包可由目前使用者選擇安裝「登入後啟動」捷徑與單一監看程序。這**不是**未登入前服務模式。Tailscale IP 變動需重新配對。

## 尚未驗證與發布界線

- 本輪**尚未在 C701 所在的遠端 Windows 電腦操作新版配對碼、登入後自啟、重新開機及長時間監控**。此前手動 RTSP 經 Tailscale 取得影格和短片，不能代替本輪配對流程的實機驗收。
- r54 是可攜候選包，並未在本輪覆寫 `C:\ProgramData\MeowKeeperPlus\App` 的目前安裝。已安裝 manifest 為 19,063 檔、Dashboard `/healthz` 為 HTTP 200；r54 為 19,065 檔。升級器會停止本機所有 `ffmpeg.exe`，但本輪對現有 PID 的 Windows 唯讀查詢未取得 ExecutablePath／CommandLine，因此無法確認它不屬於其他工作；未冒險執行升級。舊 Host 不會因為建包自動獲得配對 UI。
- 未驗證未登入 Windows 前可用，亦未驗證遠端 Tailscale 掉線後 24H 自動恢復。請勿宣稱遠端 C701 已達正式 24H 發布標準。
- 遠端 RTSP 尚無獨立密碼，應僅在受控 Tailnet 上使用；不得直接開到公共網路。對遠端 C701 的 Xiaomi MISS PTZ 仍未接線。

## 使用者唯一下一步

在 C701 旁的 Windows 電腦安裝／解壓 r2 中繼包，執行 `scripts\Start-C701Relay.cmd`、必要時由本人完成小米登入、執行 `scripts\Test-C701Relay.cmd` 與 `scripts\Export-C701Pairing.cmd`，再於 r54 Host 的 C701 卡片貼上配對碼。配對碼短效且不含小米憑證，不應貼到公開聊天。完成後才能做真正的配對、重新開機和 24H 驗收。
