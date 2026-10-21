# MeowKeeper+ AI 動作分析管理中心 V5：接線盤點

日期：2026-10-17 更新。狀態：`V5_CODE_WIRING_COMPLETE / EXTERNAL_AND_VISUAL_ACCEPTANCE_PENDING`。這是 repository 程式接線狀態，不代表正式 Host 已更新，也不代表真實 Provider、家庭影像或通知品質通過實機驗收。

## 已接線並有隔離程式測試

- `/settings/ai` 由 FastAPI/Jinja、原生 JavaScript/CSS 實作總覽、設定與額度、分析表現；沒有導入前端框架。
- Host 啟動時建立 `VisionSettingsStore`，依設定產生 managed Provider，接入 `RepositoryCoreController`／事件分析；保存或啟用設定的生效時機仍依 Host lifecycle。UI 使用登入／CSRF 保護的 API。
- 支援 Gemini GenerateContent 與 OpenAI Chat Completions 相容圖片介面。metadata 連線檢查不送圖片；圖片理解測試只在使用者確認後，以程式在本機繪製的合成示範圖呼叫，不讀取攝影機影格。
- 雲端圖片分析預設關閉；啟用需對當前 Provider／endpoint／model 明確同意。切換目的地或模型會載入各自設定並要求重新同意。Key 使用既有 Windows Credential Manager 優先；特定無互動登入工作階段可用 DPAPI 加密檔案 fallback，不寫入一般設定、SQLite、API 回應、日誌或完整資料備份。
- 視覺設定 schema v7；SQLite schema 29 的 `cooldown_ref` 遷移保留既有分析紀錄。每服務／endpoint／model 分開保存本機預算與拼貼設定。V5 尚未發布；舊的預解析度欄位不映射其 low/high 語意，新解析度欄位預設 `auto`，其餘額度、拼貼及冷卻設定保留。場景物件參考另有預設關閉的設定與獨立同意，不會因原本允許事件拼貼外送而自動開啟。
- 額度保護：每日預算 1–500（預設400）、RPM 1–60（UI預設12，UI上限15）、可選 TPM、保守 Token 預估、滑動60秒窗、SQLite原子保留、45秒工作租約、90天帳本與決策紀錄、429有界冷卻及有限試探。Gemini日界依太平洋時間；其他 Provider 目前採 UTC。服務商額度需手動輸入，並非即時同步。
- 拼貼設定實際接入既有逐軌跡事件裁切：影格數2–8（預設4）、JPEG品質60–95（預設82）、畫布1280×960／1536×1024／2048×1365、時間戳與有效區域開關。解析度選擇為 `auto/low/high`（OpenAI Chat Completions 相容服務）及 `auto/low/medium/high`（Gemini）；OpenAI 的 low/high 映射到圖片 `detail` 欄位，auto 不附加欄位。Gemini 依 low/medium/high 選擇本機拼貼畫布尺寸；auto 使用畫布上限設定。不同服務欄位不宣稱效果等價，也不代表模型一定支援圖片。
- 場景參考 UI 已恢復為即時影格／上傳照片、本機拖曳選取位置與物件類型、保存標記，以及只在本機產生的物件裁切拼貼；不再要求使用者輸入位置百分比。事件分析只有在 V0.2、全域圖片外送已開啟、場景參考另行啟用且對目前 Provider／endpoint／model 明確同意時，才會附上最多 8 個、總計最多 512 KB 的物件裁切與標籤。排除／隱私區永不送出；原圖不送出；場景參考只作物件情境，不可作為事件動作或寵物身分證據。此功能有單元與 API 接線測試，尚未用真實服務商驗證。
- 事件分析冷卻預設關閉、0–3600秒。啟用時僅用 SHA-256 scope 對同 Provider、同來源、同事件類型、同軌跡（並包含已知 pet identity）冷卻；無軌跡證據不套用，避免把其他貓的事件一併略過。只略過雲端分析，不影響本機偵測、錄影或 Telegram 原影片。
- Gemini `countTokens` 是明確點擊、登入／CSRF保護的合成圖探測；使用同一 GenerateContent body schema，不送家庭圖片或執行推論。每服務每日最多10次、每分鐘最多1次。SQLite只保存模型識別、HTTP、Token數、耗時和結果分類，不保存圖片或 prompt。
- 分析表現顯示 SQLite 實際紀錄、1小時／24小時／7天／30天區間、請求與略過、Token實際／估算／未知、平均及P95、模型比例。紀錄 API 支援 limit/offset 分頁（每頁 1–200，UI 每頁 100），提供目前區間的準確總筆數與是否有下一頁；UI 可搜尋模型、事件、貓名與動作，並依分析結果、身分紀錄、Telegram 回條篩選。頁面明示搜尋只涵蓋已載入頁面，避免冒稱搜尋整個時間區間。
- 每筆事件明細可展開平行時間軸：從已保存的 UTC 時間戳顯示 AI 排隊、圖片準備、服務/API、結果整理，與事件影片準備、AI 等待、Telegram 上傳。缺少時間點不補估。回傳只含白名單時間、標籤、狀態及耗時，不含 track ID、收件者摘要、訊息內容或媒體路徑。
- API 契約包括登入保護的 `GET /api/v1/vision/settings`、具分頁的 `GET /api/v1/vision/usage`，CSRF保護的設定 `PUT`、metadata `POST /health`、合成圖片 `POST /image-test`、合成 Token 探測 `POST /count-tokens`；Key走獨立安全憑證API。

## 仍待外部／人工驗收（不是未接線項目）

- 尚未取得真實 Gemini／OpenAI-compatible Key 執行外部連線、合成圖及實際 Provider 媒體欄位驗收；服務商／模型對圖片模式的支援差異仍須使用者自行核對。沒有驗證實際帳戶配額、費用、Token估算精度或貓咪辨識品質。
- 須在實際瀏覽器以 360／390／768／1440px 驗證新搜尋控制、載入更多、空白／錯誤及時間軸可讀性與鍵盤操作；本地自動化測試不等於人工視覺驗收。
- 本輪未對真實 Telegram Bot 做收件端 E2E，未用家庭影像外送，未建置／發布／安裝／重啟正式 Host。

## 驗證

- 定向設定／用量 API／分頁／時間軸／額度回歸：`python -m unittest tests.test_vision_settings tests.test_vision_request_guard -q`：33 tests，通過。
- 全量測試：`.venv\\Scripts\\python.exe -m unittest discover -s tests -q`：784 tests，通過（221.727 秒）。
- 全 repo Ruff：`.venv\\Scripts\\ruff.exe check src tools tests`：通過；`compileall -q src tools tests` 與 `node --check src/meowkeeper/app/web/static/js/vision-settings.js`：通過。
- 舊 schema 的新解析度預設 `auto`、provider 欄位映射、分頁總數與不重複事件、三條時間軸及敏感欄位遮罩有定向測試。
- 場景標記頁面存在、登入保護的本機拼貼端點、同意隔離、Provider 請求只附物件裁切、隱私區排除與 V0.2 呼叫條件有定向回歸；尚未做瀏覽器人工視覺驗收或真實 Provider 呼叫。
- 2026-10-17 本輪場景參考定向測試：60 項通過；完整測試 843 項通過；已知故障門檻 13 類／33 項通過，未略過；全 repo Ruff、compileall 與兩支前端 JS 語法檢查通過。這些結果只證明 repository 隔離接線，不代表真人雲端服務、實際家庭影格或瀏覽器視覺已驗收。
- 實際瀏覽器 360／390／768／1440px 視覺、鍵盤及錯誤／空白狀態驗收：尚未檢查。
- 自動化測試不代表外部 Provider 或家庭實機驗收；本輪未以真實 API Key、家庭影像或 Telegram 做端到端測試。
- 當前整體判定：`V5_CODE_WIRING_COMPLETE / EXTERNAL_AND_VISUAL_ACCEPTANCE_PENDING`；不得稱「V5全部驗收」或「已正式部署」。
