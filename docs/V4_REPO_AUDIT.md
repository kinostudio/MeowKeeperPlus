# V4.3 Gate 0：完整規格對照與施工順序

> 本文件是初始稽核快照。2026-10-15後續接線與剩餘缺口請見 [最新實裝進度](V4_3_IMPLEMENTATION_STATUS_2026-10-15.md)，勿把下方待建清單當作今日程式現況。

2026-10-14。本文件是 repository 稽核，不是正式部署或家庭影像驗收。

## 規格來源

已完整讀取 Downloads 的 `MeowKeeperPlus_GOAL_V4_3_ZeroBarrier_Telemetry_UX.md`（589 行，第 0–16 節）及 `MeowKeeperPlus_Telemetry_Help_Terms_zhTW_V4_3.json`（schema 1、36 個詞條）。先前摘要未涵蓋所有要求；不能把摘要版接線當完整 V4.3。

## 既有／待建／部署與驗證矩陣

| 規格 | 原始程式證據 | 現況與缺口 |
| --- | --- | --- |
| 0 現況稽核 | `app/web/api.py`、`templates/base.html`、`static/js/event-review.js` | 原 API/session 保留；全量 Git diff 包含大量既有修改，不整批重寫。正式 `/healthz` 本輪回覆 ok，但不證明新功能部署 |
| 1–2 單一來源、影片通知、並行與額外等待 | `core/media/event_clip.py:submit_event/_send_with_retries` | 既有 JPEG 入有界 worker；轉碼完成才等待一次 Future，再多收件者 fan-out。單張不符合時序證據要求；缺可追溯各階段 UTC/monotonic 記錄及基線 p50/p95 |
| 3 Provider、設定、故障回退 | `core/event_semantics.py`、`integrations/vision/`、`app/vision_settings.py` | 圖片adapter、deadline、設定／獨立同意／DPAPI UI與Host啟動讀取已接；尚未部署。請求保留／lease／budget／circuit與單一試探有局部測試；其他protocols、health check、完整持久化queued jobs與實機Provider待完成 |
| 4 Tag 與多段時間 | `core/event_semantics.py`、`core/events/web_review.py` | 目前 12 個 TAG 白名單；缺姿勢/區域/疑似維度、證據索引、certainty/time_quality 與 taxonomy version |
| 5 C+ 與 Provider 輸入 | `core/semantic_storyboard.py`、V02HostAnalyzer／EventSemanticService | 已接既有貓框4–6時點、編號拼貼、內部識別與alias映射；loopback HTTP驗證兩轨跡候選。物理PTZ epoch、環境物件與隱私gate／前端設定仍未完成；未允許家庭影像外送 |
| 6 PTZ 輔助登錄 | `core/behavior/zones.py`、`core/events/v02_host_analyzer.py` | 既有來源區域被載入；不能當作全視角校正。物件登錄、view/epoch 對位及 legacy unverified 遷移待建 |
| 7 同事件融合與 DB | `storage/database.py` schema22 | append-only semantic revisions、通知時序與同SQLite請求/熔斷保留已接；schema21→22重跑有局部測試，既有事件保留。請求保留不隨事件刪除消失；完整assessment/provenance、queued jobs恢復與降版驗收仍缺 |
| 8 Dashboard 播放／分段 | `templates/_event_review.html`、`core/events/web_review.py` | 多 TAG、秒數範圍、人工 revision 已接；隔離合成影片播放及两段回報已測，不是真實攝影機。缺可移動時間軸、逐候選接受/駁回及分析重跑 UI |
| 9 Telegram | `core/media/event_clip.py`、`core/events/v02_pipeline.py`、`notifications/track_tag_feedback.py` | cat_detected保留send_video；finalized另走圖片／文字。schema21身分草稿與動作僅在最終提交時形成同交易人工修訂；第一題TTL、取消、返回身分及原影片receipt授權有局部測試。編號影像一致性、完整順序導航、按鈕revision與實機回條仍待完成 |
| 10 強驗證 | `normalize_result`、`normalize_storyboard_result` | 已拒絕未知 TAG/alias、錯誤影格索引、缺失軌跡與虛構精準時間，保存粗略證據；zone／物理PTZ epoch／語意衝突驗證待完成 |
| 11 安全與備份 | `security/full_backup.py`、既有 Web auth | 未改登入與秘密；新增 provider credentials 處理、完整備份／還原／降版尚待驗證，不宣稱 schema 17 可安全降版 |
| 12–14 交付／驗收 | `reports/v43-unittest-current.txt` | 完整 630 tests OK 是程式回歸，不是實機 Provider/PTZ/Telegram。其餘要求文件與100筆聚合／10次實機耗時等未完成 |
| 15 全站白話說明 | `app/web/telemetry_help.py`、`telemetry_help_terms.json`、`_term_help.html` | 詞典原文接入 Jinja；初步於事件回報、趨勢分析使用。全站 key coverage、瀑布圖、手機與輔具驗收尚未完成；5 位真人測試 NOT_VERIFIED |

## 相容性與風險

- 新候選不能覆寫人類答案；已追加 semantic revision，舊表只保留讀取投影。仍未達完整 input/provider/epoch provenance 契約。
- 通用 worker 仍依賴 provider timeout 契約；新增 HTTP adapter 以專屬子程序 deadline 強制中止。不能用這個測試宣稱其他 provider 都 enforce deadline。
- 不把未標框的全圖配到多 Track，不從單張圖肯定動作先後；沒有對位證據不能推斷喝水／進食區域。
- Telegram 影片與 finalized 軌跡摘要是不同分支，改版需保留原去重／opt-in／receipt，不多送影片。
- 正式 installed manifest 未直接提供 version 欄位，本輪不以 health 或空欄位推定版本；Installed → Running → Real E2E 全部仍需核對。
- 新的說明 JSON 需納入 Python package data；已增加本地 JSON pattern，尚未建置發布包。

## 分 PR／Gate 順序（不等於已建立 GitHub PR）

1. PR1：版本化 taxonomy、analysis jobs/revisions、時序與 migration/備份測試。
2. PR2：Provider adapters、capabilities、設定／獨立外送同意、安全 Key、deadline/circuit/budget；mock 故障矩陣。
3. PR3：既有影格時間序列與 numbered track storyboard、品質 gate、PTZ epoch/物件/view 登錄及 legacy 遷移。
4. PR4：證據驗證、同事件融合、Dashboard 候選對照與時間軸／有效性覆核。
5. PR5：原影片自然 caption、共用 grace deadline、逐 Track 三步回報與 receipt/TTL/重放驗證。
6. PR6：DB 全量統計、0/5/10 秒策略回放、共用說明全站覆蓋、隔離瀏覽器／實機 Telegram／Windows 回歸。

每步提供 passed/failed/not_checked。正式部署由使用者自行安裝；家庭素材外送與新 Provider 同意獨立處理。模型家庭準確度、5 位新手測試與正式實機 Telegram 未驗收前，完整目標保持未完成。
