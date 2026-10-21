# MeowKeeper+｜AI 動作分析管理中心保存問題交接（供 Argon 檢測）

- 日期：2026-10-20
- 專案 GitHub：https://github.com/kinostudio/MeowKeeperPlus
- 本機分支：`codex/r110-candidate`
- HEAD：`a028de49d4cabf5e5344b8cec8a00a79d647b6cb`
- 分支名稱是歷史標籤，不能由 `r110-candidate` 推定程式仍為 r110；r111 基準核對見下節。
- 範圍：AI 動作分析管理中心保存接線、事件影片在首頁／即時頁的疊圖驗收；正式 Host 升級與發布另行執行。

> **重要：** GitHub 網址是公開專案參考。目前這個工作目錄沒有設定 Git remote，且本次修改尚未提交或推送；只看 GitHub 頁面不會看到下方列出的工作區修改。正式 Host 也尚未更新。

## 2026-10-21｜r111 基準及交接方式

本機 `dist/release-r111/MeowKeeperPlus-r111-windows-x64.zip` 內的 build manifest 與遠端正式安裝目錄 manifest SHA-256 相同：`bfc704ef293e02c20873b9c2bee905f860daf090e132e52729816c90cccc2e87`，標示版本為 `MeowKeeperPlus-portable-2026-10-20-r111`。工作區對應的290個第一方檔案沒有缺漏：271個完全相同，9個 PowerShell 腳本只有編碼／換行差異，10個內容差異對應本輪 AI 設定、疊圖、HUD 及回歸台帳修改。新工具、測試與文件另以未追蹤檔案同步。

發布包沒有 Git commit 欄位，本地也沒有 r111 tag，因此這是可核對的 r111 發布檔案基準證據，不能宣稱完成不存在的 tag／提交祖先驗證。此次比對的是磁碟上正式包，不用它推定執行中程序的版本。

每次交接前以 `tools/sync_to_argon.py` 更新 `C:\Users\user\Desktop\MeowKeeperPlus-Workspace`。工作區根目錄包含 `.argon-git-meta.json`、`ARGON_UNCOMMITTED.diff`、本交接文件與逐檔 SHA-256 manifest；本文件另複製到該帳戶桌面。詳細規則見 `docs/ARGON_HANDOFF_SYNC.md`。正式 App 不會被同步工具覆寫或重啟。

## 問題與重現線索

**後續已定位的主要根因：** 新版 `_vision_center.html` 被頁面引用，但沒有載入 `vision-settings.js`；唯一 script 標籤仍在 `settings.html` 的 `{% if false %}` 舊版區塊內。結果是摘要停在「讀取中」，載入／保存事件都未綁定。API 單元測試通過沒有覆蓋這個頁面接線缺口。

先前畫面另有輸入驗證問題：

- 服務選擇 Google Gemini。
- 模型欄填入顯示名稱 `Gemini 3.5 Flash Lite`，含空格。
- API 網址的模型路徑則是 `gemini-flash-latest`。

Gemini adapter 要求模型欄為服務商模型 ID，且網址路徑必須與模型 ID 完全相同：`/models/{model_id}:generateContent`。顯示名稱含空格不符合格式；這是額外的輸入拒絕情境，不能替代上方已找到的模組掛載根因。畫面上的模型仍未核實為目前服務商有效且支援圖片的模型。

## 工作區已有的修正

### 2026-10-21：Argon 3A／3B 補充覆核

- 3A 已在修改前重現：切換目的地時同次提交的額度品質遭忽略。現在後端先載入目標 profile，再套用 payload 的明確修改；前端切換時省略未改的舊模型 profile 欄位，保留返回模型時的既有設定。
- 3B 已在修改前重現：停用且模型留空、網址仍存在會被拒絕。現在可保存不完整草稿，但已填網址的秘密／HTTPS規則及模型格式仍驗證；不完整不能啟用，連線檢查直接提示補完且不送請求。切換離開 Gemini 也會清除自動產生的專用網址。
- 26項設定測試（包含新增4項回歸）、共59項視覺服務相關回歸通過；隔離 Edge 實際測過同次切換加修改、返回模型保留未改欄位、草稿、拒絕啟用、reload及兩種同意重置。指令：`node tests/browser/vision_settings_boundaries.cjs http://127.0.0.1:18988`；需先啟動 `tests/vision_overlay_acceptance_fixture.py --port 18988`，依環境設定 Playwright 模組位置。
- 這些是本工作區結果；其他 checkout／已安裝 r111 的實際檔案不因本次修改自動更新。
- 錯誤安全覆核另涵蓋標準庫 URL 解析例外：`integrations/vision/openai_compatible.py` 將解析失敗轉成固定繁體中文訊息，避免原始例外回顯網址輸入片段。回歸同時檢查檔案權限例外與畸形網址不洩漏測試敏感字串。

1. `src/meowkeeper/app/web/templates/_vision_center.html`
   - 直接掛載帶版本快取鍵的管理中心模組一次。
   - 保存按鈕旁增加進度／結果，首次設定明確說明模型 ID、保存 Key 與檢查連線順序。
2. `src/meowkeeper/app/web/static/js/vision-settings.js`
   - Gemini 模型 ID 格式錯誤時，在送出前顯示繁體中文原因，不再只讓使用者看到模糊的保存失敗。
   - Gemini API 網址由有效模型 ID 組成，避免送出舊網址或與模型欄不一致。
   - 留空模型時仍可保存尚未設定服務的其他選項。
3. `src/meowkeeper/app/web/api.py`
   - 設定驗證錯誤回傳安全、具體的欄位原因。
   - 本機資料讀寫失敗回傳不含本機路徑的權限提示；其他暫時性錯誤不洩漏例外內容。
4. `tests/test_vision_settings.py`、`configs/release-regressions.json`
   - 新增回歸檢查：模型顯示名稱含空格時回傳 422、提供模型識別錯誤，且不寫入設定檔。
   - `VISION-CENTER-SCRIPT-MOUNT` 檢查實際路由 HTML 真的掛載模組一次。
5. `src/meowkeeper/app/web/static/js/ai-overlay.js`、`src/meowkeeper/app/web/static/css/redesign.css`
   - 候選標籤移到偵測框外，避免遠處小目標被窄框內的長段文字遮住。
6. `tests/vision_overlay_acceptance_fixture.py`、`tests/browser/vision_overlay_acceptance.cjs`
   - 以隔離 app 的真實設定儲存進行瀏覽器保存／讀回測試；可明確傳入本機事件影片，以實際 CPU YOLO 輸出驗證首頁與即時頁。不得對正式 Host 執行此 fixture。

## 已執行檢查

- 設定／provider／request guard／transport／疊圖／MSE／Dashboard 共77項相關 Python 測試通過；其中視覺設定22項。
- `node --check src/meowkeeper/app/web/static/js/vision-settings.js`：通過。
- `python -m ruff check src/meowkeeper/app/web/api.py tests/test_vision_settings.py`：通過。
- `python -m compileall -q src/meowkeeper/app/web/api.py tests/test_vision_settings.py`：通過。

- 真實 Edge 隔離瀏覽器：保存、reload讀回、啟用／停用、無效模型阻擋，以及390／768／1440px設定頁無水平溢出通過。
- 離線事件影片在實際 `/live`、`/dashboard` 頁面完成可見偵測框、原始影格一致性、框位與候選開關驗收。身分與動作維持未知；此結果不代表 DINOv2／行為辨識或所有可見貓咪已通過。

影格、截圖、原始報告與機器檢查紀錄只留本機忽略的 `data/diagnostics/vision-overlay-acceptance-20261020/`，不應上傳公開 GitHub。以上不是正式發布包或正式攝影機端到端驗收。

## 請 Argon 檢測

1. 先確認新版 partial 掛載模組一次，不能僅檢查 API 或獨立 JS 檔案存在。再確認 Gemini 模型 ID 格式及 URL 一致性。
2. 檢查保存按鈕是否由表單 submit handler 接線，且在登入／CSRF 保護下呼叫 `PUT /api/v1/vision/settings`。
3. 驗證無效模型時，前端會指出原因且不送出；有效 ID 的 Gemini URL 會自動匹配；尚未設定模型時可保存其他非啟用選項。
4. 驗證後端輸入驗證錯誤、資料目錄不可寫及一般暫時性錯誤的狀態碼與使用者訊息；不得回傳 API Key、DPAPI 內容、完整本機路徑或其他秘密。
5. 回歸確認 OpenAI 格式相容服務的保存、金鑰獨立安全保存、CSRF、圖片外送同意及模型切換時不沿用舊同意。
6. 可先執行上列隔離測試；如測試 Gemini 實際連線，僅使用使用者在本機已安全保存的 Key，不要求、不輸出、不複製 Key。模型有效性／圖片能力須以當前服務商文件與明確授權的連線檢查確認。
7. 回報分開標示：程式碼檢查、隔離測試、瀏覽器端到端、候選包、正式 Host；沒有證據的項目標示 `not_checked`。

## 尚未完成／不可宣稱

- 正式 Host 的保存錯誤尚未以當次 HTTP 回應確認；目前無已登入的正式設定頁供端到端重現。
- 隔離登入瀏覽器操作已完成；正式 Host 更新後的功能操作仍待驗收。
- 尚未對 Google Gemini 發出即時連線或圖片請求；沒有確認畫面中的模型目前有效或支援圖片。
- 尚未建置候選包、發布 GitHub Release 或安裝到任何 Host。
- 尚未提交／推送本次修改；此工作目錄 `git remote -v` 無輸出。

不得把隔離測試結果描述成已在正式 Host 修復。Argon 若需驗證本次修正，應以可讀取此工作區的方式檢查；若只能讀公開 GitHub，需先由專案維護者決定後續提交／推送流程。
