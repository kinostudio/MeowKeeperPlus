# Telegram 遠端控制：2026-09-29 驗收進度

目標仍為附件 `d7ee3a17-f619-406d-b809-f990aa968313/pasted-text-1.txt` 的完整
`Code → Package → Installed → Running → Real Telegram E2E`，沒有縮減為 UI 或單元測試通過。

## r37 建置與安裝等待（歷史驗證）

後續新增 SQLite schema 8 每日取樣統計；目前 repository 已超前 r37。
以下 305 tests／195 檔比對僅對應當時 r37，不證明最新修改已打包或安裝。
下一次安裝應先建立並驗證包含每日統計的新包，不直接重試舊 r37。

- 全部 305 unittest、全範圍 Ruff、compileall 通過。
- r37 可攜包 18,684 檔，內附 runtime 執行 verifier 回傳 passed，private_data_present=false。
- manifest 對照 repository 的 195 個 src/tools 檔案：0 個不一致。
- repository 與包內第一方程式／文件的 Telegram Token、真實 Xiaomi 帳號 URL 格式掃描各為 0 命中；不把有限格式掃描擴張成所有可能秘密已排除。
- 已執行 SYSTEM `-Upgrade`，並確認安裝器 PID 13052、Windows consent PID 34316 同時存活；安裝當時仍指向 r35，尚未宣稱 r37 已安裝。
- 可恢復狀態：`reports/telegram-remote-control/progress.json`。現有安裝器等待 UAC 時不得重複啟動第二個安裝程序。
- 後續同一 tool session 結束碼 1：Windows `Start-Process -Verb RunAs` 回覆 `The operation was canceled by the user`。安裝器與 consent PID 已不存在；這證明本次提升權限流程結束，不證明使用者有意按了取消。正式 manifest 仍為 r35、healthz 仍不可用；沒有宣稱 r37 已安裝或啟動。待使用者可核准 UAC 時重試已驗證的同一 r37 包，不建立新版本、不重做建置與測試。

## 本輪直接查證

- 正式 App 的 `build_manifest.json` 指向 r35；repository 有 r36 後續修正及 Mock 預覽空狀態修正。
- 本輪開始時 localhost `/healthz` 無法連線，未觀察到 8000／1984／8554 listener。
- 標準使用者 `schtasks /Query /TN "MeowKeeperPlus Core"` 回覆 Access denied；不能把它解讀成 Core 工作不存在。
- SYSTEM 權限與目前 Codex shell 不同，`require_escalated` 的沙箱授權不等於取得 Windows 管理者身分。
- 保存設定 `telegram.enabled=true` 只能證明設定，不能證明 poller 或 API 已接通。

## 本輪程式修正

- 未登錄的真實私人 sender 送命令時，經共用有界出站佇列回覆沒有權限，並記錄匿名 user_ref 與 `TELEGRAM_UNAUTHORIZED`。
- 群組、sender/chat 不一致、bot sender 不發拒絕訊息，不呼叫相機服務。
- `/snapshot` 使用既有來源狀態與近期 frame timestamp；來源不在 connected/running/streaming 或影格超過 20 秒時，回覆 `FRAME_UNAVAILABLE`，不傳舊 JPEG。
- `/snapshot` Caption 加入安全來源類型；不輸出來源 URL。
- Mock／尚無影格頁面顯示明確空狀態，收到 JPEG 才顯示圖片，避免瀏覽器破圖 X。

## 每日統計接線驗收

最新 repository：316 unittest 通過（72.824 秒）、全範圍 Ruff 通過、compileall 通過。
不是 r37 包或正式 r35 的測試結果；新版尚待建置／安裝。

- `DailyMonitoringRecorder` 只累計相鄰有效影格：長間隔、時鐘倒退、來源切換不補算。
- schema 8 `monitoring_batches` 以 UUID 批次冪等寫入；預設 30 秒 flush、有界 8 批佇列與單一背景 writer，媒體執行緒不做 SQLite 寫入。
- 只有相鄰兩個分析均確認 cat 才累計出現取樣時間；未知分析保持 null，已分析無貓才是 0。
- `/today` 使用保存的取樣總量，不等同實際玩耍／活動；休息、進食、飲水等未分析欄位仍是 null。
- 正常停止 flush；崩潰可能遺失尚未保存批次，佇列滿或資料庫失敗的批次不推估補回。
- 新測試涵蓋跨日、重啟讀回、真實 SQLite 暫鎖、慢 writer、失敗遮罩、冷卻後 raw detection。
- 真實 bundled FFmpeg 解碼本地 testsrc MP4，取得 JPEG 並將有效影格取樣時間保存後重新開啟資料庫讀回；使用 Noop 時 cat 時間仍未知。
- 此本地影片驗證不替代真實相機、YOLO 24H 或 Telegram message_id 驗收。

## 完整目標的剩餘驗收

| 項目 | 本輪程式證據 | 最終證据仍缺 |
|---|---|---|
| Command Router、角色授權、選單 | `telegram_commands.py`、單一 `TelegramFeedbackPoller` | 新版 Host 真實 Bot 命令與鍵盤回條 |
| `/status`、啟動通知 | 共用 `RuntimeStatusService`；健康、近期影格、資源及安全網址 | 正式執行版即時回覆與 message_id |
| `/home` | 動態 LAN／Tailscale 安全 URL；未開 LAN 僅顯示候選 | 實際可連線 Dashboard URL 按鈕 |
| `/snapshot` | 共享 JPEG、斷線／過期拒絕，不新增來源 | 真實相機 JPEG、ok=true、message_id、producer 不變 |
| `/live` | 共享 ring buffer、15 秒近期匯出、有界媒體工作 | 可播放 MP4、真實 duration/message_id、ring 不清空、producer 不變 |
| `/hide`、`/show` | `PrivacyService` 只有 adapter 邊界，實際 Host 未注入 camera adapter | 真實相機隱私能力、狀態回讀、冪等性及恢復驗收；目前尚未完成 |
| `/today` | detector／模擬告警分開；schema 8 批次取樣持久化與重啟讀回；未知行為 null | 新包安裝、真實相機全天取樣及 Telegram 回覆；未保存／斷線時間仍未知 |
| `/reset` | 角色、inline 確認、durable update、graceful Host restart、重啟後健康回報 | 真實 Telegram 確認後重啟與新 Host 健康、回條 |
| 版本與單一實例 | package verifier、PID/port audit、installed verifier 工具存在 | r37 安裝、執行中來源路径與版本一致，Tray／Host／go2rtc 實機核對 |
| 安全與資源 | 共用有界佇列、有限重試、匿名 log、秘密儲存 | 正式 log/SQLite 秘密掃描及實際拒絕權限操作 |

`/hide`、`/show` 尚未接上真實 adapter；每日取樣已具持久化程式與本地驗證，但正式相機全天資料與 Telegram 回條仍缺，不因發布新包而自動完成。
只有每一項實機條件都取得證據，才能將完整 Telegram 控制面板目標標記完成。

## r38 獨立建置驗證

- `dist/MeowKeeperPlus-portable-2026-09-29-r38` 建置完成；未覆寫 r37 或 ProgramData App／Data。
- 內附 Python 以 `-B tools/verify_portable.py --root ...` 驗證 passed：18,685 檔、manifest 列示總大小 1,954,001,651 bytes。
- 模型 SHA-256：`0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`。
- package manifest 對照 repository 196 個 src/tools 檔案：0 個 hash 不一致。
- 包內 src/tools/docs 的 Token 格式與數字 Xiaomi account URL 格式掃描皆 0 命中；僅為指定格式邊界，不宣稱排除所有可能秘密。
- 正式 localhost healthz 仍不可用；r38 尚未安裝，也沒有真實 Bot message_id。
- 部署前新增必要修正：`RuntimeStatusService.snapshot()` 的 camera.connected 目前只採近期 timestamp，尚未同時確認來源 running/connected/streaming；不符合附件兩條件綠燈規則。r38 先保留為可驗證建置快照，不把它宣稱完整發布候選或立即安裝。
- 下一步只補此狀態 guard 與斷線但有近期快取的測試，再進行新版部署及真實 Telegram E2E；不新增攝影機 backend、不重試已取消的 UAC。

## 後續 repository 修正：相機狀態 guard

- `RuntimeStatusService` 現在同時要求 source.status 為 running／connected／streaming 與帶時區的近期影格；斷線、停止、重連、錯誤及未知來源，即使留有近期快取，也不顯示相機綠燈。
- `/status` 與 startup notice 共用此判定；錯誤說明改為「來源未連線或無近期影格」，不把仍有快取說成沒有影格。
- 未知、損壞、無時區、過期或未來影格時間皆 fail-closed；`/snapshot` 同步拒絕无時區時間。
- status guard 完整 318 unittest 通過（73.909 秒）；最後 snapshot 時區修正後重跑 33 Telegram tests 通過（7.045 秒），Ruff、compileall 通過。
- r38 建置時尚未含此修正；package 雜湊一致是歷史快照，現在 repository 再次超前。不得修改已驗證 r38 內容或聲稱 installed/running 已更新。
- 正式 localhost healthz 本輪仍不可用。下一步建立包含此修正的新包，再由真正 Windows 提升權限流程部署；隱私 adapter 與真實 Telegram E2E 仍未完成。

## r39 建置與部署 gate

- 最後全部修改後重新跑完整 318 tests：78.610 秒、0 failures；全範圍 Ruff 與 compileall 通過。
- 獨立 `dist/MeowKeeperPlus-portable-2026-09-29-r39` 建置完成；沒有覆寫 r37／r38／正式 App／Data。
- 包內 Python `-B tools/verify_portable.py` passed：18,685 檔、manifest 列示 1,954,004,249 bytes、private_data_present=false。
- repository／package 196 個 src/tools 雜湊全部一致；已包含每日取樣 schema 8、Mock 空狀態、status active-source guard 與 snapshot timezone guard。
- 包內第一方 src/tools/docs 指定 Telegram Token 與數字 Xiaomi account URL 格式掃描：各 0 命中；不宣稱為所有秘密模式的全面證明。
- 本輪唯讀核對 Windows Administrator=false；Core 排程查詢 Access denied、正式 healthz unavailable、未觀察到 ProgramData App runtime 程序。不能以 Access denied 宣稱排程不存在。
- 尚未啟動新 UAC／安裝程序：已詢問使用者是否目前可親自核准 UAC，等待 readiness；不繞過提升權限、不重複先前取消的安裝。
- 可部署不等於完整目標已完成：真實相機 privacy adapter、Telegram JPEG／MP4 message_id、實際 reset 與正式單一實例仍待驗收。

## r39 真實 runtime 隔離啟動與關閉

- 沒有重試 UAC。使用 r39 包內 `runtime/python/python.exe` 執行真正 `tools/run_host.py`，獨立暫存 config／SQLite／lock／AppData、隨機 loopback port，`--disable-monitor`、Telegram disabled，不讀正式秘密、不啟動相機或第二個來源。
- 成功測試 PID 3144、port 59014；healthz HTTP 200，未授權 status／JPEG／events API 全部 HTTP 401，SQLite schema 8／WAL。
- 子程序內定時 KeyboardInterrupt 經 Host 原有正常關閉流程：安全關閉訊息存在、exit 0、無 forced termination，listener 釋放，暫存狀態清理完成；上述 PID／port 已不是正在運行的服務。
- 第一輪 Host 也正常關閉，但檢查工具自己的 SQLite connection 未顯式 close，造成工具暫存清理失敗；已修正工具、重跑 exit 0，並移除該輪工具的無秘密暫存目錄。沒有因這項工具錯誤修改 Host 或發布包。
- 證據：`reports/telegram-remote-control/r39-isolated-host-smoke.json`；可重跑 harness 在本 Codex workspace 的 `r39_isolated_host_smoke.py`。
- 本證據只證明 r39 embedded runtime 可啟動真實 Web Host 與正常釋放資源，不證明 SYSTEM 安裝、正式憑證可讀、相機影像或真實 Telegram 命令閉環。

## 可恢復阻塞：需使用者親自核准 Windows 提升權限

- 同一部署條件已連續至少三個 goal turn 存在：目前 shell 非管理員、正式仍 r35、healthz 不可用、未收到可重試 UAC 的 readiness 回覆。先前安裝／consent 與隔離 Host 都已結束，沒有可繼續等待的活程序 handle。
- 不依賴 SYSTEM 權限的相關程式修正、完整測試、r39 包驗證與隔離真實 Host 啟動／授權拒絕／正常關閉已完成；不以反覆重建、重測或重述同一狀態取代實機進度。
- goal 設為 blocked 而非 complete，保留整份附件原目標與 r39 可恢復入口。使用者回覆「現在可以核准 UAC」後，才啟動同一已驗證 r39 升級並續驗 installed/running/Telegram E2E。
- `/hide`／`/show` 仍未接上實體 adapter：現有整合只提供 PTZ 動作，沒有已驗證的相機原生 privacy API／安全隱私位置。不能新增附件禁止的攝影機 backend／PTZ、猜測安全角度或用停止 AI 假裝相機隱私成功；正式 runtime 恢復後需核對實機能力，若仍無合法現有路線再提出單一範圍決策。
# 後續 MISS 隱私接線更新

本日後續 repository 更新見 `XIAOMI_MISS_PRIVACY_MODES.md`：已建立指定位置隱私
Adapter，Dashboard 與 Telegram 共用服務，保存／讀回／恢復位置；C701 關閉／待機
可保存偏好，但原生開關控制未接通。此更新超前 r39；下方原先「未接線」敘述為
歷史狀態，實機 hide/show、standby、SYSTEM 發布與 Telegram E2E 仍未驗收。

## 2026-09-30 真實 Telegram 執行版驗收更新

本節為本日實測，覆寫前述 r39「正式 Host 離線／等待 UAC」的歷史狀態；不代表完整目標已完成。

- ProgramData `build_manifest.json` 指向 r40；已核對 MISS privacy adapter、Dashboard live template、`run_host.py` 與 r40／repository 的雜湊一致。Host 的 `Win32_Process` 詳細資訊查詢遭 Access Denied，因此**正在執行的 Python executable 完整路徑及全包 running-version 一致性仍未證明**。
- 本機 `/healthz` HTTP 200，`core_status=running`；go2rtc API HTTP 200、v1.9.14。安全化 `/api/streams` 摘要在本輪媒體命令前後均為 2 streams、2 producers、2 consumers，未增加 producer。
- 在 `@MeowKeeperplusbot` 私人對話真實收到 `/status` 回覆；狀態顯示來源 running、有新 640×360 影格、約 5 FPS、YOLO 可用、go2rtc 可用、SQLite schema 8／WAL。回覆含動態內網候選但標示僅本機監聽；本報告不記錄 IP 或 PID。
- 真實 `/snapshot` 回覆附有相機 JPEG，caption 顯示 640×360、來源 rtsp。
- 真實 `/live` 回覆附有來自既有共享環形快取的 15.0 秒 MP4；Telegram Web 顯示可播放影片。前後 producer／consumer 計數不變，ring buffer 未被命令清空。
- 透過常駐 Reply Keyboard 的 `/today` 按鈕取得真實摘要；監控與有效影格取樣時間有數值，行為時數仍明確標為尚未分析，未產生假統計。
- 透過常駐 Reply Keyboard 的 `/home` 按鈕取得真實回覆；因 Dashboard 仍只綁定 loopback，且本機未觀察到 Tailscale service／CLI／介面，正確回覆目前沒有可安全分享的 LAN／Tailscale URL。未為求通過而暴露 LAN。
- 常駐鍵盤實際顯示 `/start`、`/snapshot`、`/live`、`/status`、`/today`、`/hide`、`/show`、`/reset`、`/home`；使用 `/today`、`/home` 按鈕均確實送出 canonical command 並收到回覆。
- 真實 `/reset` 請求被拒：「此 Telegram 帳號沒有權限執行此操作。」沒有重啟 Host。這驗證了操作權限防線，但未驗證成功重啟流程；不可自行把此帳號升級為 Operator／Admin。
- `/hide`／`/show` 本輪未執行：`/status` 明確顯示尚未設定已驗證的相機隱私位置，且目前帳號沒有 Operator/Admin 權限。實體隱私控制仍未驗收。
- Bot 的狀態、JPEG、MP4、今日摘要及 `/home` 回覆均在 Telegram Web 可見；但正式 Host 的命令 API receipt（`api_ok=true` 與 `message_id`）只記在受保護的 Host 記憶體日誌。本機 Dashboard 日誌頁要求管理者登入，本輪沒有登入憑證，因此 message_id **未由 Codex 獨立擷取**。不得把 UI 可見訊息寫成已查得 API message_id。

### 尚待完成的必要操作

1. 使用本機 Dashboard 管理者登入後，在 Telegram 通知對象設定將要測試 `/reset`、`/hide`、`/show` 的帳號明確設為 Operator 或 Admin；不要把 Chat ID／密碼貼到 Codex。若不願提高權限，這些控制命令應維持拒絕。
2. 由使用者選定並在 Dashboard 設定可逆且安全的相機隱私位置，確認對應目前實際攝影機；不要猜角度或把停 AI 當作鏡頭隱私。
3. 若要讓 `/home` 可分享，使用者需明確啟用 LAN Dashboard 存取或先建立並驗證 Tailscale；目前 loopback-only 設定不應被自動放寬。
4. 管理者登入後才可從受保護日誌擷取本輪命令的 `api_ok=true`／`message_id` 安全回條；不需要提供任何 Token、密碼或完整 Chat ID。
5. 完成上述操作後再驗收 `/reset` graceful restart／health、`/hide`／`/show` 實際相機狀態讀回，以及正式執行版本的 executable 路徑和單一實例；目前目標保持未完成。
