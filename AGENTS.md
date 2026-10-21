# MeowKeeper+ repository instructions
## Permanent Rules

- 品牌固定為「喵喵守護者+ / MeowKeeper+」；副標為「MeowKeeper+ — Local AI Pet Guardian」。
- GUI、使用者錯誤、操作提示與日誌一律使用繁體中文；不得把寵物姓名、RTSP 路徑或未知硬體寫死。
- 24H 服務與 GUI 分離；每部攝影機只維持必要的單一 RTSP 輸入並在本地分流。
- Runtime priority：影音／快取 → 貓與動作／行為 → 事件／Telegram → 身分 → 統計。
- 身分與行為未完成家庭實拍驗收時一律標為 experimental；身分失敗先保存 `unknown`，不得阻塞主路徑。
- 單一 Bot 支援多 Chat ID；秘密不得進 Git、日誌、報告或發布包。
- ONVIF 預設唯讀，不得自行修改攝影機影像、網路、帳號、韌體或 mydlink 設定。
- 長期資料用 SQLite；正式資料庫需 schema version、可重跑 migration、WAL、busy timeout、保留與容量限制。

## Hardware Baseline

- `mx250_minimum`：MX250、2 GiB VRAM 預算、8 GiB RAM 最低、16 GiB 建議；16 GiB 不自動提高負載。
- 單一視覺模型、batch 1、640 像素級、約 5 FPS 為基本基線，不是硬上限或各機保證。尊重已保存的自動／CPU／Intel／NVIDIA偏好；實際後端與偏好分開顯示，GPU失敗安全降級CPU。高解析度與負載目標須目標硬體實測。
- 高規格開發機結果不得當成 MX250 效能證明；目前模型／行為與身分結果需實機驗收。

## Coding Principles

- 修改前先理解現有架構、資料流與完成條件。
- 優先採用能滿足目前版本的最簡單方案。
- 僅修改本次任務必要的檔案，不進行無關重構。
- 完成後依變更範圍執行相關檢查，並依本次驗收條件確認。
- 不得捏造硬體、帳密與外部狀態；低風險、可逆的實作細節可採合理假設並註明，涉及權限或範圍變更時先確認。

## Working Method

- 簡單、局部任務偏好較低 reasoning；架構決策與困難除錯才提高。此為選擇偏好，不代表已切換實際模型或 reasoning。
- 非必要不啟用 MCP、瀏覽器或 subagent。
- Codex 與 Argon 以同一 GitHub repository 的分支與完整 commit SHA 協作；各自使用 Git clone，交接前 fetch 並確認乾淨工作區。交接包需列基準／完成 SHA、全部來源與測試檔案的 SHA-256、驗證結果。`tools/sync_to_argon.py` 僅供明確指定的舊式檔案交接目錄，不得覆寫 Git clone；方法見 `docs/ARGON_HANDOFF_SYNC.md`。
- 公開 Git 僅包含程式與一般說明；實機影像、硬體量測、帳號、裝置位址、操作紀錄及私人交接資料保存於 Git 忽略的本機 Data。發布前同時檢查索引與歷史基準，不能只檢查新增檔案。
- MeowKeeper+ 後續升級一律提供可複製的安裝代碼與精確發布包路徑，由使用者自行執行；不得自動啟動安裝器、觸發 UAC 或升級正式 Host。
- 系統 Python、驅動或 CUDA 變更需另行確認；僅可安裝任務已授權的專案依賴或模型，遵守安全與供應鏈規則。
- Windows 發布包須含 Python runtime、FFmpeg、必要套件與預設模型；終端使用者不得建立 venv、執行 pip 或手動下載模型。
- 外部程序需逾時、檢查結束碼、`shell=False`，且輸出先遮罩秘密。

## Autonomous Completion

- 任務已授權的 repository-local runtime 依賴可安裝至既有 `.venv`，並按需記錄至正式 extras；無法安裝時如實報告驗收限制。
- 命令失敗先讀取已遮罩 stderr；只有新證據或修正改變失敗條件才重試，不重複相同失敗，不因失敗擴大任務範圍。
- 如實標示 `passed`／`failed`／`not_checked` 與原因；必要項目未驗證不得宣稱完成。權限、網路、外部依賴或範圍限制亦可阻擋。
- 長任務按需保存可恢復進度；僅修改啟動器或相關 runtime 時，執行實際 Windows shell 啟動驗證；唯讀或純文件任務不因此重啟服務。
- GOAL 任務持續至完成條件通過或真正 blocked；不要求使用者守在電腦前執行已授權的 repository-local 操作。

## Document Routing
- 故障診斷、重試或完成宣稱前，按主題查 `docs/LESSONS_LEARNED.md`；歷史結果不得當成現況，不需每次載入全文。
- Dashboard UI／普通使用者說明與視覺驗收：`docs/UI_QUALITY_STANDARD.md`；難懂選項需就地說明用途與影響，驗證手機版、鍵盤、空白及錯誤狀態。
- 產品／MVP 驗收：`docs/PRD.md`、`docs/MVP_RELEASE_GATE_REPORT.md`
- 最新需求／接線／產品化：`docs/PRODUCTIZATION_SPEC.md`；當前執行順序：`docs/CURRENT_GOALS.md`。舊版本報告是歷史證據，不是今天的部署狀態。
- 重複故障與發布防退化：`docs/ENGINEERING_FEEDBACK_LOOP.md`、`configs/release-regressions.json`。
- 程序／媒體／資料流：`docs/ARCHITECTURE.md`
- 行為與統計：`docs/BEHAVIOR_MODEL.md`
- 秘密與供應鏈：`docs/SECURITY.md`
- 硬體／RTSP／Phase 0：`docs/COMPATIBILITY.md`
- 版本順序：`docs/ROADMAP.md`
- 測試與完成條件：`docs/TESTING.md`
- V0.2 experimental：`docs/V0.2_IDENTITY_BEHAVIOR_PLAN.md`；24H：`docs/V0.2B_24H_INFERENCE_REPORT.md`；Windows 包：`docs/BUILD_WINDOWS.md`
- Phase 3 Web／Tray／遠端：`docs/MVP_PHASE3_WEB_DASHBOARD_PLAN.md`、`docs/MVP_PHASE3_WEB_DASHBOARD_REPORT.md`、`docs/TAILSCALE_INTEGRATION.md`、`docs/XIAOMI_GO2RTC_INTEGRATION.md`、`docs/XIAOMI_MJSXJ03HL_FIRMWARE_OPTIONS.md`；3C.2：`docs/MVP_PHASE3C2_XIAOMI_DEVICE_SYNC_REPORT.md`；3C.4：`docs/MVP_PHASE3C4_SINGLE_INSTANCE_REPORT.md`、`docs/MVP_PHASE3C4_XIAOMI_P2P_REPORT.md`（證據：`reports/phase3c4/`）
- Phase 3D YouTube source：`docs/MVP_PHASE3D_YOUTUBE_SOURCE_REPORT.md`；固定版 yt-dlp、yt-dlp-ejs 與公開來源 adapter 的完整契約留在該文件。
- 只讀取目前任務需要的文件，不重複載入全部規格。

## Verification
- 局部修改執行相關檢查；純文件修改檢查內容與連結。跨模組變更、發布驗收或使用者明確要求時才執行完整測試：`python -m unittest discover -s tests -v`、`python -m ruff check src tools tests`、`python -m compileall -q src tools tests`。不得宣稱未驗證功能已支援。
- 已解決且會重犯的故障，需連到可執行回歸並登記故障台帳；只寫LESSONS不算閉環。正式建置與簽署前須通過本版故障回歸門檻；失敗、略過、逾時或程式異動使舊證據失效時不得放行，不以刪測試／改台帳掩蓋失敗。
- README只保留產品狀態與最新證據入口；AGENTS保留跨版本規則，不逐版堆PID、版本路徑或一次性任務。改變產品範圍／預設／資料契約時同步更新PRD與當前目標，不能因單元測試通過就勾掉真實外部驗收。

## GitHub Authorization Boundary

- 本專案的 GitHub repository 是 `kinostudio/MeowKeeperPlus`；本專案固定使用全域 active GitHub CLI 帳號 `kinostudio`。
- 任何需要 GitHub 認證的 `gh` 或 `git` 操作前，先確認 `whoami`。隔離 sandbox 不代表已授權 Windows 使用者的 keyring；sandbox 中的 401／403 不得視為使用者授權已撤銷。
- 在隔離 sandbox 遇到 401／403，不得自動執行 `gh auth login`／`gh auth logout`、刪除 hosts 設定或重置憑證。
- Sandbox 無法讀取或代表使用者的 keyring。需要 GitHub 認證時，改由已授權的 Windows 執行環境操作；不得把 keyring、Token 或 `GH_TOKEN` 複製到 sandbox、檔案、提示或日誌。
- 認證操作前後，以 `whoami` 和 `gh api user --jq .login`（不輸出 Token）確認執行身分及 GitHub 帳號 `kinostudio`。若暫存 `GH_TOKEN`，僅限程序生命週期，並於結束時清除。
- 回報時分別列出執行身分、keyring 可見性、GitHub API 驗證及實際 Git／Release 操作結果；未驗證項目標示 `not_checked`。
