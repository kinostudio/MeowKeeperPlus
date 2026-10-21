# Telegram 遠端控制面板實作與驗收

本文件追蹤附件 `pasted-text-1.txt` 的原始範圍。Telegram 是 Dashboard 的遠端操作入口；
程式碼、可攜包、已安裝版本、正在執行的 Host 與真實 Telegram E2E 分開驗收。

2026-09-29 最新逐項 gate 見 [本輪驗收進度](TELEGRAM_REMOTE_CONTROL_GATE_2026-09-29.md)；
它覆寫下方歷史 runtime 結論。實機 `/hide`／`/show` 與完整 Telegram E2E 尚未完成。

## 最新安裝驗證（2026-09-28，r34）

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。

## 歷史驗收差異（2026-09-28，r32 升級後）

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。

## 原始需求階段

| 階段 | 內容 | 目前證據／狀態 |
|---|---|---|
| A | 持續命令選單 | 原始碼已在同一個 poller 確認無 webhook 後呼叫 `setMyCommands`／設定 menu button；單元測試通過；真實 Bot 尚未驗收 |
| B | 單一 Update poller、Command Router、Viewer／Operator／Admin 授權 | 原始碼已接線；私人 chat、sender/chat 一致及角色權限皆先檢查；本機整合測試通過 |
| C | `/status`、`/home` | 已讀目前 Host metrics、近期影格、go2rtc 健康、SQLite、資源與動態安全 Dashboard URL；本機測試通過 |
| D | `/snapshot` | 使用現有共享 JPEG，不啟動來源或建立 producer；本機測試通過 |
| E | `/live` | 使用既有 ring buffer、獨立有界片段工作佇列及共享 Telegram outbound queue；本機測試通過 |
| F | `/hide`、`/show` | 命令已經 PrivacyService 抽象；沒有實機驗證的隱私／恢復位置或 camera adapter 時 fail-closed，不移動鏡頭；尚未具備實際控制能力 |
| G | `/today` | 只計算 SQLite 中真實 detector 與 simulated 告警，未驗收的行為時間保持 unavailable/null；本機測試通過 |
| H | `/reset` | Operator/Admin 確認、SQLite 原子消耗 callback update、Host graceful restart、下一個通過健康檢查的 Host 回報；本機整合測試通過 |
| I | Host startup notification | 已與 `/status` 共用 `RuntimeStatusService.snapshot()`；啟動摘要在真實 `/healthz` 通過後產生，含近期影格、AI、go2rtc、資源與安全 LAN／Tailscale URL；本機測試通過，真實 Telegram 尚未驗收 |
| J | 真實 Telegram＋實機 E2E | 尚未以包含本輪 Command Router 的安裝／執行版本按實際按鈕驗收；不能以舊版 outbound message_id 代替 |

## 安全與架構不變條件

2026-09-28 r36：已合併上述 Reply Keyboard、前次 go2rtc 啟動依賴修正及內網候選 URL 顯示；302 unittest、Ruff、compileall（臨時 pycache prefix）通過。可攜包與內附 verifier 通過，18,683 檔、private_data_present=false。未安裝／未實機重啟驗收，因此仍不得宣稱 SYSTEM Host 已自動恢復 go2rtc 或真實影像。本輪唯讀 LAN 探測得到 Wi-Fi IPv4 `192.168.31.15`；這是暫時觀察，不寫死於設定或程式。loopback bind 未變更，LAN 候選不等於手機已可連線。

後續待發布修正：Host 讀回已保存的 loopback RTSP 來源時，先檢查並恢復同 port 的既有 go2rtc 服務，再啟動 ingest；健康實例直接重用，不新建第二個程序。缺少既有 config 時不建立空設定、不清除 Session、不切回 Mock。來源／串流沿用既有持久化 config，不重新登入或掃描 profile。另讓共用 NetworkService 提供實際內網候選 URL；loopback listener 的候選網址僅標示需啟用區網，不放入可分享 URLs。Tailscale 沒有經驗證的 remote URL 時明確標為未可用。本機單元驗證通過，不等於正式 SYSTEM 重啟後已恢復影像。

2026-09-28：含 Reply Keyboard 的 r35 可攜包已建立；297 unittest、Ruff 通過。compileall 改用臨時 pycache prefix 後通過（預設快取路徑因沙箱權限拒絕）。r35 內附 verifier 通過，18,683 檔、private_data_present=false。正式運行版仍為 r34；按鈕鍵盤需安裝 r35 並在 Bot 傳送 `/start` 後進行真實 UI／API 回條驗收。

- 後續原始碼新增對話框 Reply Keyboard（不同於斜線 command menu）：已授權者傳送 `/start` 或一般文字命令回覆時，附上常駐、自動縮放按鈕鍵盤。Viewer 顯示截圖、影片、狀態、摘要、Dashboard、本人 Chat ID；Operator/Admin 加上隱私、恢復、重啟。未知使用者只有本人 Chat ID 按鈕。按鈕文字轉回 canonical command 後仍經相同授權；偽造控制按鈕文字不會繞過權限。Reset 保留二次 inline confirmation，Dashboard 保留 inline URL，不覆蓋其 markup。此修改尚未包含已安裝 r34，不能宣稱目前 Bot 已顯示鍵盤。

- 新使用者在私人 Bot 聊天按「開始」或傳送 `/start`，由現有單一 poller／outbound queue 自動回覆本人的 Chat ID 與 Dashboard 設定引導；不自動批准通知／監看／操作。Bot 不會僅因使用者搜尋或開啟聊天而主動傳訊息，需使用者先按開始。此流程需 Host 的 Telegram receiver 已启用（目前仍沿用至少一位既有已啟用通知對象的啟動條件）；新增對象不另建 poller。此新增功能尚未包含於已建置的 r33／正式 r32，需下一次發布及真實 `/start` 回條驗收。

- 一個 Bot、一個 getUpdates worker；回饋 callback 與命令共用 poller，不建立第二個 poller。
- 指令只接受已啟用的私人 Chat ID；Viewer／Operator／Admin 由本機 Credential Manager 通知對象 metadata 管理。
- Token 只留在既有秘密儲存；Chat ID、Token、Cookie、完整 source URL 不進日誌或命令回覆。
- `/snapshot` 讀共享最新影格；`/live` 只讀既有 ring buffer；不得另建 FFmpeg 或 camera consumer。
- 狀態回覆以程序、健康 API、近期影格與實際 metrics 為據；未知值必須顯示 unavailable。
- `/hide`／`/show` 需要使用者明確設定並驗證可逆的隱私位置／adapter；沒有設定時回報 unavailable，不猜測攝影機方向。
- `/reset` 必須有角色授權、二次確認、graceful restart 與重啟後 health check；命令受理不等於重啟成功。
- `/reset` 的確認 update 與資料庫 offset 必須原子提交；重啟不得等待 Telegram 額外確認訊息送達。
- `/hide`／`/show` 透過 `PrivacyService`，只接受已設定且由相機回報成功的 adapter；不得因 PTZ 指令已送出就回報成功。
- `/today` 對沒有來源資料的休息、進食、喝水、玩耍時間回傳 unavailable/null，不能以 0 假裝已分析。

## 本機完成與外部驗收界線

- 命令路由、自動化測試與 SQLite reset 記錄屬於原始碼層；安裝包、目前執行 Host 與真實 Bot callback 仍需分別驗收。
- `RuntimeStatusService` 是 `/status` 與 Host 啟動摘要共用的即時資料來源；它讀取目前 Host metrics，並以 localhost `/healthz`、go2rtc API／RTSP 健康、近期 frame timestamp、系統資源、SQLite 與動態網路位址判定狀態。缺少資料或探測失敗時顯示 unavailable，不把設定值當成運行成功。
- `/hide`／`/show` 的軟體服務邊界已建立，但相機安全位置未配置，因此不列為已實作的實體隱私控制。
- 貓咪「是／不是」回饋會保存人工標籤並更新 reviewed-alert precision／Wilson 區間；這只是已告警樣本的 precision，不會自動重訓或調門檻。

## Definition of Done

每個命令需具授權與拒絕測試、服務成功／不可用測試及無秘密洩漏檢查。全部原始需求完成後，
還需逐層證明 `Code → Package → Installed → Running → Real Telegram E2E`；單元測試、API
`ok=true` 或舊版出站 message_id 均不能替代按命令、按鈕、真實畫面、攝影機狀態及重啟健康的驗收。

2026-09-28：共用 StatusService 接線後，全套 287 unittest、Ruff、compileall 通過；r31 可攜包內附 verifier 通過（18,683 個檔案、私人資料檢查 false），包內 runtime 檔案與目前原始碼一致。ProgramData 程序資訊因 CIM 權限拒絕無法證明版本；`/healthz` HTTP 200 只證明有服務回應，不代表它是最新包。r31 尚未安裝；真實 Telegram callback、媒體及 reset E2E 仍為 `not_checked`。
