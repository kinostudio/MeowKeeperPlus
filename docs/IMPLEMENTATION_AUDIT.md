# AI 身分工程 Gate 0（2026-10-14）

依交付 V1 規格對照目前原始碼；正式 Host 未部署、家庭 holdout 未驗收。

| 契約 | 現況與位置 | 動作 |
|---|---|---|
| YOLO／tracker／共享裁切 | `core/events/v02_host_analyzer.py`、`core/events/v02_pipeline.py` | REUSE：維持單一 ingest |
| DINOv2 | `core/identity/dinov2_embedder.py` 官方固定模型 | REUSE：不重訓 YOLO |
| 特徵庫／回饋特徵 | `pet_registry.py`、`feedback_learning.py` | MODIFY：版本／有限數值／來源快照與人為同意 |
| 單張候選 | `identity_classifier.py`、`photo_comparison.py` | MODIFY：統一融合排序／近似照／Unknown 原因 |
| 多影格投票 | `temporal_identity.py` | MODIFY：unknown 分母、去重、時間跨度、安全衝突，不沿用舊 stable 當新證據 |
| 背景佇列 | `background_resolver.py` 共用有界 scheduler | REUSE：加入證據 fingerprint，不開新 ingest |
| 保存推論 | SQLite `behavior_events`、新增 `identity_inference_history` | MODIFY：原始候選證據與人工修訂分表；有保留限制，不逐影格寫入 |
| 人工回報 | `web_review.py`、`telegram_feedback.py`、SQLite annotation history | REUSE：不覆寫 AI 推論、保留兩階段與授權 |
| 單張測試 API | POST `/api/v1/pets/compare-photo` Session／CSRF | REUSE：不保存上傳圖片、不加入 gallery |
| 參考照片 API | GET `/api/v1/pets/{pet_id}/images/{image_id}` | REUSE：受控查找，不回傳任意路徑 |
| 人工回報影片 | GET `/api/v1/events/{event_id}/video?playback=true` | REUSE：有界 H.264/AAC 臨時副本 |
| 首頁／事件候選證據 | 共用 `identity_view`、`identity-detail.js` | NEW：既有頁面增量卡片，不新增側欄中心；本輪視覺驗收尚未完成 |
| 家庭校準 | 無獨立 holdout 報告可證明門檻 | NOT_VERIFIED：候選不可當確認身分 |
| 正式部署 | 安裝版不包含本輪比對 UI | NOT_VERIFIED：不得自行部署 |

## 實作順序

PR1 單影格安全與候選契約 → PR2 獨立證據共識／持久化 → PR3 受保護共用 API → PR4 三頁候選 UI → PR5 共用回報／gallery 同意與修訂 → PR6 家庭 holdout 與使用者自行安裝驗收。

驗證：相關 identity、V0.2、feedback、Web tests；跨模組完成後全 unittest、Ruff、compileall。Windows 僅隔離啟動驗證，不操作正式 Host、攝影機或 Telegram。

規格的 API／表名為提案，需以本表既有服務適配。Runtime priority 遵守 AGENTS.md：影音快取 → 貓／動作 → 事件／Telegram → 身分 → 統計；不採用規格中不同的文字排列來變更優先級。
