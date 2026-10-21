# Xiaomi SG／JPEG 與 SYSTEM 升級 SOP（2026-09-24）

> 歷史紀錄，不是目前操作授權。文內「代理自動升級／不用使用者執行」的舊指示已被後來的使用者要求與 AGENTS.md 取代：只提供精確發布包路徑及安裝代碼，由使用者安裝；不得自動觸發 UAC、安裝或升級正式 Host。歷史成功不能取代本輪驗收，見 [經驗手冊](LESSONS_LEARNED.md)。

## r32 安裝看似卡住：QuickEdit（2026-09-28）

- 真實證據：管理員 PowerShell PID 13868 的視窗標題是「選取 系統管理員: Windows PowerShell」。安裝器 PID 33268 是其 CMD 子程序的子程序；安裝後完整性校驗 Python PID 31716 的 parent 為 33268，CPU 累計 20.09375 秒不再增加，執行緒在等待。當時沒有 schtasks 子程序；因此不能判定為排程註冊卡住。
- r32 manifest 已出現在 ProgramData App，僅證明檔案已複製；沒有第二次驗證結果、Host 健康回應或完成訊息時，不代表安裝通過。
- 恢復：在原管理員 PowerShell 按一次 Esc 解除選取，讓原校驗及安裝續跑；不用重跑升級。完成後仍須驗證 Host／Tray、manifest 與 `/healthz`。
- 原始碼加入安裝期間暫停 QuickEdit、finally 恢復原 console mode 的防護，並增加複製後校驗的進度提示。此修正尚未進入已建好的 r32，也不能改寫 r32 manifest 假裝已包含。

本文件記錄**已實測**的接通條件、曾造成假失敗的命令，以及 SYSTEM 升級踩到的檔案占用。它不是「台灣帳號一律使用 SG」的規則。不得把帳號、Token、DID、Cookie、MAC、完整攝影機 IP、`xiaomi://` 來源或私人影格寫進報告。

## 已驗證與未驗證

| 項目 | 本輪證據／狀態 |
| --- | --- |
| 歷史成功 | 2026-09-20 `chuangmi.camera.026c02` 的 SG／SD 串流透過官方 go2rtc v1.9.14 取得 51,857-byte JPEG；見 [Region／MIoT SOP](XIAOMI_MIOT_REGION_SOP.md)。 |
| 本輪 SG 裝置清單 | 已登入 Session 的 SG 清單包含 `.55` 目標型號；CN 或 SG「列出裝置」本身不證明串流可用。 |
| 本輪真實影格 | 在限縮的臨時 UDP 入站規則下，內附 FFmpeg 從同一條 localhost go2rtc RTSP 暫時來源取得有效 640×360 JPEG，54,113 bytes，SHA-256 `23d806e4b50f181378ab33efff51f968efeaba3cae7f3f57f39ad7ca59dc262d`。影像未落盤，暫時 stream 已刪除。 |
| 臨時規則 | 測後已移除，`netsh` 查無該規則。這證明**在臨時網路條件下**串流可用，不證明無規則時可 24H 運行。 |
| go2rtc JPEG API | `/api/frame.jpeg` 在本輪曾回 HTTP 500，與 FFmpeg 轉碼有關；不可因此斷言來源未出影格。程式現在先試 API，失敗時再用發布包內附 FFmpeg、同一條本機 stream 作後備驗證。兩個 consumer **依序**執行，不同時拉攝影機第二條來源。 |
| SYSTEM App | r9 包已完整安裝於 `C:\ProgramData\MeowKeeperPlus\App`，embedded runtime 的 `verify_portable.py` 通過。Core／Tray 排程已啟動，`/healthz` 回 HTTP 200；安裝版的真實 JPEG 與持續串流尚未驗收。 |
| 常駐 UDP 規則 | 使用者明確同意後已建立並核對 `MeowKeeper+ Xiaomi .55 UDP`：僅安裝版 `go2rtc.exe`、來源 `.55`、UDP、Public Profile。此規則常駐，非先前已移除的臨時規則。 |

## 安全接通順序

1. 先確認 SYSTEM App／go2rtc 只有一個實例、API 與 RTSP 只綁 localhost。讀取已登入帳號的區域清單，**僅在同一次 SG 清單中找到同一型號及目標裝置**時，使用其記憶體內來源；不要寫死 DID 或完整 URL。
2. 自動區域探索優先使用真實 JPEG 驗證，成功才把該裝置的 region 雜湊對應寫入 cache。若所有影格驗證失敗，而 SG 清單確實有同一裝置，正式 Host 僅顯示「SG 候選」，開放一次 SD 畫質測試，**不**標記已驗證、**不**直接接入。
3. 建立一個名稱安全的 `subtype=sd` 暫時 stream，優先向 go2rtc JPEG API 要一張影格；若 API 失敗或回空內容，才用 `vendor/ffmpeg/bin/ffmpeg.exe` 讀**同一** `rtsp://127.0.0.1:<RTSP Port>/<暫時 stream 名稱>`，輸出單張 `image2pipe` MJPEG。檢查 JPEG SOI／EOI、大小與尺寸；無論成功失敗均刪除暫時 stream。
4. 命令須有 `-nostdin`、`-rtsp_transport tcp`、`-frames:v 1`、子程序逾時及 `shell=False`。本輪曾把 `-rw_timeout` 傳給內附 FFmpeg 8.1.2，立即得到 `Option rw_timeout not found`；那次 0-byte 結果是**錯誤測試命令**，不是攝影機無影像。後備影格命令已移除該參數，靠子程序逾時控管。
5. 若開流出現 `read udp ... i/o timeout`，先核對 Windows 網路 Profile、實際 go2rtc 執行路徑與防火牆規則的 `Program` 路徑。舊規則曾指向 repository 的 `vendor/go2rtc`，而 SYSTEM 安裝版從 ProgramData 執行；規則名稱相同也不代表套用到新執行檔。不可盲目重啟攝影機、換 Profile、改 Xiaomi Cloud 或套 PR。
6. 只有獲得**常駐規則**的明確授權，才可建立限制為安裝版 `go2rtc.exe`、單一攝影機來源位址、UDP 與必要 Windows Profile 的入站規則。新增後核對 Program、RemoteIP、Protocol、Profile；不要開放全部程式、全部 IP 或任意 TCP 服務。移除時按精確規則名稱刪除，保留可回復方法。

## SYSTEM 升級：避免再次刪到一半

1. 升級前驗證新包 `tools/verify_portable.py --root <package>` 與目前可回退的舊包，並對新包 `scripts/Install-MeowKeeperPlus.ps1 -WhatIf -Upgrade -SkipStart` 乾跑。資料保存在 `C:\ProgramData\MeowKeeperPlus\Data`，升級不得移除。
2. 自 2026-10-09 原始碼修正起，SYSTEM `-Upgrade` 僅停止完整路徑確認位於舊 MeowKeeper+ App 的 `ffmpeg.exe`／`ffprobe.exe`；停止前重新核對 PID、建立時間及命令列。其他軟體的 FFmpeg 不得被選取。舊發布包曾包含按名稱清理全部 FFmpeg 的行為，該行為已撤除；請使用包含此修正的新包，不能只憑舊包的警告文字認為安全。
3. 2026-09-24 首次升級中，安裝器結束排程後，舊 App 內一個先前 localhost 診斷留下的 `ffprobe.exe` 仍在執行，`Remove-Item` 刪到 `ffprobe.exe` 時被拒絕，App 只剩部分檔案、Dashboard 停止。該程序的 PID、安裝版完整路徑與建立時間均核對後才單獨停止；重跑安裝成功。**這是檔案占用，不是模型或小米帳號問題。**
4. 若程序身分資訊不足、停止失敗或舊 App 的媒體程序仍存活，升級必須在移除舊 App 前中止。修改安裝器原始碼不會修改已產生的發布包；須重新建置、驗證新包後才可用它升級。
5. 安裝包的 `-SkipStart` 只完成複製、驗證與註冊排程，不等於 Core 正在運作。必須再取得管理員確認，啟動 Core／Tray，驗證 `/healthz`、唯一 Host／Tray／go2rtc PID、內附 runtime 路徑與 Dashboard 畫面。若 UAC 被取消，應如實標記未啟動，不得把安裝成功等同服務可用。
6. PowerShell 診斷 wrapper 若另外啟動子 PowerShell 執行 `.ps1`，須與正式安裝器一致使用 process-scope `-ExecutionPolicy Bypass`；本輪曾因漏掉它而只得到「指令碼執行已停用」，那次重試**沒有**進入安裝流程。

## 自動執行邊界與目前下一步

使用者不應被要求自行點擊 CMD、複製貼上 PowerShell 或手動執行 `schtasks`。執行代理／安裝器應自動完成發布包驗證、升級、排程註冊與啟動、健康檢查；只有 Windows 管理員權限（UAC）必須由使用者在系統提示中親自批准，不得繞過。取消 UAC 時停止並明確回報，不把它當成默許。

目前 r9 已安裝、Core／Tray 已啟動、Dashboard 健康檢查通過，限縮常駐 UDP 規則已建立。下一步是核對真實 Host／Tray／go2rtc 程序，再以已安裝的 SYSTEM runtime 驗證同一台攝影機的 SG／SD 真實 JPEG；在取得影格前，不宣稱正式 Dashboard 畫面或 24H 串流已完成。r10 是含媒體占用預檢的下次升級包，不因其已建置就宣稱已部署。

## 2026-09-24 Dashboard 接入狀態修正

- `測試畫質` 證明單一 SD stream 可由 FFprobe 讀到影像資訊，**不等於** MeowKeeper+ 的 YOLO 與環形快取已運行。`即時畫面` 有圖也要再核對最近影格時間、AI FPS、壓縮片段數及來源設定。
- 舊版接入流程把核心的非同步 `starting` 狀態覆蓋成失敗，並只更新記憶體中的來源，造成「即時畫面可見／接入報錯／設定仍是 mock」的矛盾。修正後接受 `starting` 為**待確認**，將單一本機 go2rtc RTSP 來源保存到非秘密設定，總覽仍依真實影格及 AI 指標決定是否顯示「正在監控」。
- Dashboard 管理者登入與 Xiaomi Mi Home 登入是不同 Session；總覽不應再次索取小米帳密。go2rtc 的 Xiaomi Session 位於使用者或 SYSTEM 的 AppData 設定中，升級 App 不得刪除此資料。若既有 go2rtc config 的 port 無法安全辨識，啟動器須保留原檔並 fail closed，絕不能用空 config 覆寫。
- `isa.camera.hlc7` 本輪仍只有 SG 候選，單一 SD 測試沒有穩定影格；不可把另一台 `chuangmi.camera.026c02` 的成功推論為它也成功。畫質錯誤應展示已遮罩的具體原因，不連續自動重試。
- 上述修正目前只在 repository 原始碼；部署前不得把它寫成已安裝版的實測結果。升級仍需 Windows UAC 由使用者親自批准，代理可自動執行其餘命令，但不得代按 UAC。
- 修正版 r11 可攜包已建置並通過 `verify_portable.py`（18,658 個檔案；未包含私人資料），但**尚未替換** ProgramData 中的 r9。這台機器目前命令執行身分為 Windows Medium Integrity；不能自行批准 UAC。不要把 r11 單元測試結果說成已安裝版的實機驗收。

## r11 安裝中斷紀錄（2026-09-24）

- r11 `verify_portable.py` 與 `-WhatIf -Upgrade` 皆通過，目標 App／Data 與兩個排程名稱正確。
- 首次正式 `-Upgrade` 回傳 exit 1，沒有可用子程序輸出；Dashboard `127.0.0.1:8000/healthz` 隨後不可用。**真正的第一個錯誤原因尚未確認，不得猜成 Xiaomi 登入或模型失敗。**
- 事後提升權限的唯讀 audit 證實：SYSTEM `go2rtc.exe` 仍為安裝目錄內單一 PID，持有 localhost 1984／8554；Core／Tray 排程存在但狀態為 Ready（未執行）；未見安裝目錄內 FFmpeg／FFprobe。小米 API 帳號數仍為 1。安裝目錄 `run_host.py` SHA-256 與 r11 不同，故沒有證據表明 r11 已部署。
- 一般權限執行 `schtasks /Run /TN "MeowKeeperPlus Core"` 無法找到受保護的工作，不能以此恢復 SYSTEM Host。後續有 transcript 的提升程序因 Windows UAC 回報「操作被使用者取消」而根本未啟動；不要將它當成新的安裝失敗或反覆重試。
- 下一步僅在使用者可以親自確認 UAC 時，先執行精確 SYSTEM 程序 audit／取得安裝器錯誤，再安全恢復 Core，然後決定是否完成 r11。不得依程序名稱批次停止 Python，也不得刪除 `C:\ProgramData\MeowKeeperPlus\Data` 或 SYSTEM go2rtc Session。

## r11 實際部署結果（2026-09-24，接續上段）

- 使用者親自核准 UAC 後，先以已安裝 Core 排程恢復 Dashboard，`/healthz` 回 HTTP 200。恢復腳本初版把排程執行檔誤認為 `pythonw.exe`；實際 XML 的 Core 使用內附 `python.exe`。修正後才啟動，不應把此檢查失敗當成 Core 損壞。
- Windows PowerShell 5.1 讀取無 BOM UTF-8 的繁體中文診斷腳本會誤解碼並在執行前發生語法錯誤。兩個臨時 wrapper 改用 ASCII 訊息後，實際安裝才開始；這些前置錯誤沒有觸及產品資料。
- 有 transcript 的 r11 `-Upgrade` 完成發布包驗證、舊 App 替換、安裝目錄完整性驗證、Core／Tray 排程註冊與啟動。安裝版 `tools/run_host.py` SHA-256 與 r11 包相同；Core 使用 `C:\ProgramData\MeowKeeperPlus\App\runtime\python\python.exe`，Dashboard `/healthz` HTTP 200。
- 安裝器最後 exit 3，原因是即時健康驗證當下 Host=1、Tray=0，並非 App 拷貝或 Dashboard 失敗。排程的 Tray 上次結果為 0、狀態 Ready；當時一個舊 repository `.venv` Tray 已持有同名單一實例 mutex。核對完整 executable path、`run_tray.py` 命令列與程序建立時間後，只停止該舊 PID，再以安裝版內附 `pythonw.exe` 啟動 Tray；新 Tray 五秒後仍存活且路徑、腳本均符合安裝版。
- `C:\ProgramData\MeowKeeperPlus\Data` 未被升級程序刪除。升級後尚未重新啟動 go2rtc，也尚未驗證 Xiaomi account_count、兩台攝影機影格、AI FPS 或環形快取；不可把 r11 部署與 Tray 恢復寫成小米監控閉環已通過。
- 詳細程序與錯誤證據在 `reports/r11-upgrade-transcript.txt`、`reports/r11-recovery.json`、`reports/r11-upgrade-diagnostic.json`。PowerShell transcript 僅應在確認已遮罩、無帳密後分享；不要原樣公開私人設定。

## 舊 Tray 重複占用的永久修正進度（2026-09-24）

- 排程、使用者／系統 Startup、Registry Run／RunOnce 的唯讀稽核未找到舊 `.venv` Tray 自動啟動項；真正衝突的是仍存活的 repository 開發 Tray。`reports/r11-legacy-tray-audit.json` 僅保存安全的排程名稱及路徑摘要。
- r12 升級前刻意以本 repository `.venv` 啟動單一舊 Tray；安裝器先核對執行檔、`run_tray.py` 命令列、建立時間與即時 PID，然後自行停止它。`reports/r12-upgrade-transcript.txt` 記錄清理成功；r12 安裝器 exit 0，當下 Host=1、Tray=1、Dashboard HTTP 200。這驗證了**升級時舊開發 Tray 清理**，不是所有重啟情境都已驗證。
- 後續觀察到排程啟動的 Tray 未持續存活，手動使用安裝版 `pythonw.exe` 啟動的 Tray 則存活。永久修正版讓 Tray 排程使用內附 `pythonw.exe --background`，安裝器先等待 Core `/healthz` 再啟動 Tray；安裝版 Tray 在 Core 尚未就緒時只做有限等待，不尋找 repository `.venv` 啟動第二個 Host。SYSTEM 升級也會安全停止排程以外手動啟動的安裝版 Tray。原始碼目錄的兩個啟動器在無參數、已有 SYSTEM 安裝時沿用安裝版，不再啟動開發 `.venv` 程序。
- 上述完整修正已建成 `dist/MeowKeeperPlus-portable-2026-09-24-r14`，`verify_portable.py`、dry-run、33 項相關測試、Ruff、Python 編譯及 PowerShell 語法檢查通過。第一次 r14 UAC 曾被取消；後續使用者重新授權後安裝成功， transcript 位於 `reports/r14-upgrade-transcript.txt`。
- r14 安裝驗證通過：安裝包完整性 18,664 檔、無私人資料；Core=1（PID 9148）、Tray=1（PID 20236）、Dashboard `/healthz` HTTP 200；正式 Python 均使用 ProgramData 內附 runtime。15 秒後 Tray PID 20236 仍存活，舊 repository `.venv` Tray=0。安裝版安裝器 SHA-256 與 r14 包內相符，Data 目錄仍存在。go2rtc=0，表示本次部署沒有啟動 Xiaomi producer；小米登入 Session／影格與 24H 串流須另行驗證。
- 這是升級與短時間存活驗收，尚未跨 Windows 登出／重新開機驗證 Tray 的登入觸發。下次升級前先核對唯一 Core／Tray、完整 executable path 及端口 owner；不得依程序名稱批次終止 Python。

## HLC7 FFmpeg fallback 與 r15 升級狀態（2026-09-26）

- HLC7 `subtype=sd` 已在同一條 go2rtc localhost RTSP/TCP 來源由內附 FFmpeg 實際取得有效 640×360 JPEG；go2rtc `/api/frame.jpeg` 仍可能回 HTTP 500，FFprobe 也可能因 HEVC reference-frame 警告失敗，不能把這兩個結果當成「沒有影格」。
- `src/meowkeeper/integrations/xiaomi/device_discovery.py` 的 profile probe 現在會在 FFprobe 失敗後，序列化使用同一暫時 stream 的內附 FFmpeg，驗證 JPEG 標頭與尺寸後才標記穩定；不增加攝影機連線、不做 profile sweep。
- r15 可攜式包已建置於 `dist/MeowKeeperPlus-portable-2026-09-26-r15`，`verify_portable.py` 通過，18,666 檔案，模型 hash 與 r14 相同，無私人資料。
- 本輪執行 r15 `-Upgrade` 時 UAC 尚未核准，系統安裝目錄仍維持 r14（manifest built_at 為 2026-09-24）；沒有覆寫 App、沒有修改 Data、沒有註冊新工作。要讓正式 SYSTEM Host 使用 r15，必須由管理員明確重新執行 r15 安裝器並核准 UAC。
