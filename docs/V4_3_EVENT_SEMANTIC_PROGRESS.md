# V4.3 事件影片語意與回報進度

> 最新更新2026-10-16：[三項實裝缺口接線驗收](V4_3_THREE_GAPS_ACCEPTANCE_2026-10-16.md)。schema25取消／退讓、登錄工作台及有效回報投影已接；下方舊矩陣保留為歷史，不能據此重做已完成項目。

> 2026-10-15 更新：下方早期矩陣保留為施工歷史；schema24工作帳本、第二Provider、視角／物件、覆核與全期間統計等最新狀態見 [本輪實裝報告](V4_3_IMPLEMENTATION_STATUS_2026-10-15.md)。整體仍有實裝缺口，未發布、未正式E2E。

這是待完成目標的實作進度，不是 Windows 正式 Host 或真實 Telegram 驗收完成宣告。

## 完成條件與目前接線

| 需求 | 目前狀態 | 證據／仍需工作 |
| --- | --- | --- |
| 沿用單一攝影機 ingest、共享影格與事件影片 | 程式接線完成 | `EventClipService.submit_event` 使用事件原有 JPEG；不新增來源或解碼程序 |
| 事件觸發時儘早分析代表圖，與錄影／DINO／轉碼並行 | 部分完成 | V0.2既有貓框cache→背景最多等2秒→4–6張拼貼與alias/evidence映射已接；物理PTZ view epoch、背壓與家庭辨識尚未完成，不能宣稱理解整段影片 |
| 可選的視覺 API | 部分完成 | 圖片adapter／時序輸入、Host讀取、設定與安全DPAPI UI已接；設定與Guard有局部驗證，正式部署／真實Provider／其他protocols仍未完成 |
| 候選 TAG 与簡明動作摘要 | 部分完成 | 只接受已知 TAG／本事件 track，丟棄自由文字；caption 以 TAG 組成自然中文；實際 API 結果待驗收 |
| 影片原本可發送後才額外等 AI | 程式接線完成 | 相容性驗證與必要轉碼完成後才呼叫 Future.result |
| 預設等 5 秒、可設定 0–10、硬上限 10 | 部分完成 | constructor 已驗證範圍；Dashboard 設定 UI 待接 |
| 逾時立即發既有影片及本機 caption | 程式接線完成 | 等待不取消背景結果；候選稍後可保存；保留 send_video 上傳與原 width/height/duration |
| 簡短通知先說誰做了什麼，不新增查看影片 CTA | 程式接線完成 | 本機身分候選或「貓咪」＋動作；保留時間與影片區間 |
| 候選獨立保存、不能覆寫人工回報 | 部分完成 | schema 18 追加每事件版本、同 job 重送冪等、拒絕修改歷史；input/provider/epoch provenance 仍待接 |
| Dashboard 逐段覆核修正 | 部分完成 | 候選顯示、多 TAG、秒數範圍與人工歷史已接；完整時間軸及逐候選接受／駁回待建 |
| Telegram 逐 Track 身分單選、動作多選 | 部分完成 | schema23新入口身分草稿→多選→確認提交；按鈕revision防雙擊/舊提交、兩SQLite連線競爭與22→23重跑有測試。順序導航、影像編號、真實點擊仍待驗收 |
| 全站白話說明與效能圖表 | 部分完成 | 36 詞共用 JSON 已接事件／分析兩處；完整 telemetry、全站 coverage、瀑布圖與真人測試待建／待驗收 |
| 效能統計與真實送出分母 | 部分完成 | HTTP helper送出證據、未知／未送出區分與job→caption對應已接；最近100筆平均／p50／p95有合成SQLite驗證，analytics窄版有檢查。per-provider/model、全部terminal持久化、回放與真實性能仍缺 |
| 真實供應商、真實影片通知、點擊落庫 | not_checked | 尚未設定外部 AI、未更新正式 Host、未替代真實 Telegram E2E |

## 接線與邊界

- `core/event_semantics.py` 是獨立共用契約，避免 `events.__init__` 導致媒體／偵測循環匯入。
- 每個 service 一個 worker，預設最多 8 筆等待工作、JPEG 最大 4 MB；API timeout 契約預設 20 秒、最多 30 秒。實際 adapter 必須在 transport 設定逾時，不能以這份契約當成已驗收網路逾時。
- 預設 `vision_provider=None`，不呼叫外部 API；啟用、影格外送同意與憑證設定仍需正式 UI。
- API 任意自由文字不入庫、不進日誌或 Telegram caption；身分仍由本地 registry／既有 DINO 證據處理。
- 未完成的 API 結果不阻塞影片；notification worker 的额外等待最多 10 秒，不在 ingest 或 AI 推論迴圈等待。
- 晚到候選保存與人工回報維持不同資料源，不自動作為訓練標籤。
- schema 18 保留舊候選投影，歷史追加至 semantic revisions；17→18 遷移不補造遺失歷史或耗時。SQLite snapshot 還原測試不等於完整加密備份／正式降版。
- Worker 已記錄 UTC queued/start/response/finish 與 monotonic queue/API/validation。schema22接上原影片sendable、實際grace與每次上傳耗時，事件review頁可查看；沒有回應不填假的response。完整跨job瀑布圖、佇列／退避、JPEG fallback分母與100筆聚合仍缺，見 `V4_DELIVERY_TIMELINE.md`。

## 驗證紀錄

2026-10-15 schema23完整回歸：701 tests、169.129秒、OK，輸出 `reports/v43-callback-revision-unittest.txt`。最末重送識別與stale提示補強另以19項局部重跑確認；全量Ruff/compileall passed。詳見 `V4_TEST_REPORT.md`，不代表真實Telegram E2E或正式部署完成。

2026-10-15最新完整回歸：695 tests、155.018秒、OK，輸出於 `reports/v43-performance-unittest.txt`；其後UTC Z與marker失敗路徑的局部修正已複測，未冒充新增一次全量結果。全量Ruff、compileall、JS語法檢查passed。100筆合成統計、請求證據及analytics窄版驗證見 `V4_SEMANTIC_PERFORMANCE.md`。原影片逐Track入口見 `V4_TELEGRAM_SEMANTIC_AND_MULTICHOICE.md`。正式部署與真實Provider/Telegram仍未完成。

最新多時點接線完整回歸為 654 tests、158.809 秒、OK；保留各次結果與明確驗收邊界於 `V4_TEST_REPORT.md`。這不是實際家庭辨識或正式部署完成。

2026-10-14 最新完整執行：647 tests、150.708 秒、OK；全量 Ruff／compileall passed。schema 18 及本機 HTTP adapter 的詳細失敗原因、修正與驗證邊界見 `V4_TEST_REPORT.md`。舊 `reports/v43-unittest-current.txt` 的 630 筆為歷史輸出，不當作本輪結果。

新測試 `tests/test_event_semantics.py` 覆蓋：單 worker／滿佇列、立即返回、未知 TAG 與虛構 track 拒絕、schema 16→17 重跑、轉碼後才開始等待、慢 AI 本機 caption 回退、保留 send_video 及 metadata、10 秒上限。

`reports/v43-unittest-current.txt` 最新保存結果為 630 tests、185.342 秒、OK，取代先前 620 tests 的失败紀錄；這不是本輪改動後重跑完整套件。本輪共用詞典新增兩項局部測試 passed，相關 Python Ruff 与 JavaScript node --check passed。全站瀏覽器、實際 API、正式 Telegram、真人 UX 未驗收。

隔離合成 H.264 素材曾驗證影片播放、兩隻測試貓的多選動作、0–2.5 與 2.5–5 秒兩次回報及重新載入歷史；390px 無水平溢出。這不是家庭真實貓咪／外部 API／Telegram E2E。完整規格差距與施工順序見 `V4_REPO_AUDIT.md`；詞典接入範圍見 `V4_UX_HELP_AND_PLAIN_LANGUAGE.md`。

後續 API adapter 使用圖片 content parts 與 JSON 結果；欄位依官方 [Chat Completions 文件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) 核對。尚未建立或驗收 adapter，不以文件證明真實 API 可用。多軌跡 API 前仍須建立影格編號與軌跡映射，避免將未標記的圖片任意配到 track。

這些測試不代表實際供應商辨識品質、Windows 長期運行或 Telegram 收件端已通過。
