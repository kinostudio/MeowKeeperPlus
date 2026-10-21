# r86 Xiaomi 串流、PTZ 與隱私待機修正

## 原始證據與根因

使用者在另一台 C701 同區網 Windows 筆電提供：Public profile、目前 PTZ binary 的 Private Allow／Public Block 衝突。這不是登入失敗或必須走 Tailscale 的證據。

第二輪程序盤點確認 FFmpeg 正連同一 PTZ go2rtc 的 loopback RTSP；同名串流清單卻連續十次 consumer=0，PTZ HTTP 503。原始碼 PUT 會 New 並替換 Stream，既有 consumer 可留在舊物件；PTZ 以名稱查新物件。使用者停止／開始監控後回報 PTZ 正常，支持這項診斷。回報「操作已送出」不等於本次新版已驗收馬達位置。

## 修正

- 相同完整來源登錄先查清單，在可確認來源完全相同時不再 PUT；比較只留在記憶體。
- 畫質切換先暫停主要 consumer，再建立／驗證新畫質，經既有 source_switch 重連監控；失敗回復舊畫質並重連。相同名稱不再被誤當成同一活動物件。
- 來源模式不依 C701 型號推定。現有本地路徑判定依實際 loopback RTSP port／stream；遠端需已配對 URL 與目前來源完全相符。未新增自動跨機配對或暴露管理 API。
- 隱私待機先保存原角度；恢復開關後有限等待 PTZ 重連，移回與讀回確認後才清除紀錄。開關 on 本身不再提前清除待恢復狀態。失敗可重試、可跨 Host 重啟恢復；舊版沒有原座標不能補猜。
- 防火牆支援核准 PTZ manifest、目前 profile Block 候選與其他 profile Allow 診斷。修復另需指定規則同意及 UAC；備份、失敗復原、原 Block 復原入口，不自動放寬防火牆。

## 即時畫面手動保存

- 新增「截圖」與「保存前 15 秒」，需登入並通過 CSRF；手動保存不發 Telegram。
- 截圖使用單一 ingest 的共享預覽 JPEG；影片使用既有壓縮 ring 的前 15 秒、後 0 秒，不新開來源。影片仍由既有 FFmpeg 組片／驗證路徑產生。
- 檔案位於 Host 資料主目錄 `photo/`、`video/`；可攜版通常為包內 `data`，SYSTEM 安裝為 `C:\ProgramData\MeowKeeperPlus\Data`，不寫入受保護 App 目錄。
- 顯示實際影片秒數；啟動初期不足或 GOP 邊界可能造成差異。單次保存鎖防止無限並發；低於 256 MiB 剩餘空間拒絕保存。手動檔案不自動輪替，使用者需定期整理。
- 本輪以本機生成 18 秒 H.264 合法測試影片驗證壓縮 ring 組片、MP4 可讀及保存；不是 C701 真實鏡頭操作證据。

## 2026-10-12 本輪驗證

- 全部 unittest：512 項，116.644 秒，passed。新手動保存測試 6 項包含合法生成 H.264 本地影片的真實 FFmpeg 壓縮 ring／MP4 驗證。
- Ruff、compileall、即時頁內 JavaScript 語法檢查：passed。修改範圍秘密樣式掃描：0 命中（不是所有秘密形式的形式化保證）。
- Windows 包：`dist/MeowKeeperPlus-portable-2026-10-11-r86`，19,296 檔；完整 manifest 校驗 passed，private_data_present=false。10 個關鍵程式／腳本與來源逐項正規化比對一致。
- 包內 Python 實際 Windows CMD 啟動：Host PID 25276，`runtime/python/python.exe`，隔離 Dashboard 18788、go2rtc 21988／28558。/healthz=200，未登入 API=401；登入測試頁 /live=200 且含保存按鈕。
- 測試 Host 已停止、18788 listener=0；未啟動安裝器、未變更正式 Host、防火牆或攝影機。TTY Ctrl+C 未停止該測試程序，最後核對包內完整 executable 後僅停止本輪 PID；不將此步宣稱為正常 graceful shutdown 驗證。
- 首次完整回歸有資源統計 lifecycle 測試失敗；真實 Windows 程序盤點超過兩秒 shutdown join。該測試目的為 auth／storage／lifecycle，改用固定程序盤點輸入後完整通過。**實機 telemetry 程序盤點的關閉延遲沒有在本次修正，不以隔離測試聲稱已解決。**

## 待實機驗收

C701 筆電的新版畫質切換後 PTZ、隱私待機位置恢復、UAC 與真實防火牆修復仍需使用者安裝後驗收，不能以 Mock 冒充。正式 Host 不自動升級，不要求重新輸入小米 Session。手機窄版與全螢幕保存按鈕的實際瀏覽器視覺檢查尚未完成；沿用會換行且至少 44px 的既有工具列樣式。

升級（由使用者在管理員 PowerShell 執行）：

```powershell
$package = 'C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\dist\MeowKeeperPlus-portable-2026-10-11-r86'
& "$package\Install-MeowKeeperPlus.cmd" -Upgrade
```

此報告為 repository 驗收紀錄；不可在建置後直接修改發布包內檔案或 manifest。r86 包內的報告保留建置時快照。
