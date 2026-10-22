# MeowKeeper+ 交接報告（SHRIMP-ASUS → aero15 Codex / GitHub）
**日期**：2026-10-22
**發起節點**：SHRIMP-ASUS（實機即時測試環境）
**目標節點**：aero15 / Codex 工作區 / GitHub upstream
**基準版本**：MeowKeeper+ r111 安裝版本基準（`C:\ProgramData\MeowKeeperPlus\App\src`）
**交付包路徑**：`C:\Users\user\Desktop\HandoffToCodex-2026-10-22`
**GitHub 遠端**：`https://github.com/kinostudio/MeowKeeperPlus`（branch: `main`）

---

## 交付內容與使用方式

本目錄包含由 SHRIMP 實機實測驗收通過的完整修正與功能更新，Codex 可選擇以下任一種方式套用並推送到 GitHub：

### 方式 A：使用標準 Git Patch（推薦）
在 aero15 的 MeowKeeperPlus Git repo 根目錄執行：
```bash
git apply --check meowkeeper-r111-to-local.patch
git apply meowkeeper-r111-to-local.patch
git status
git commit -m "feat(identity,vision,ptz): fix live unknown identity, add semantic traits candidate, and PTZ HUD restore"
git push origin main
```

### 方式 B：直接檔案覆蓋
將 `files/` 資料夾內的 `src/`、`tools/` 與 `tests/` 直接複製覆蓋到專案對應目錄。

---

## 本次重點功能與修復摘要（共 8 大項）

### 1. 特徵庫淘汰機制修復（保護人工標註／已入庫特徵）
* 檔案：`src/meowkeeper/core/identity/feedback_learning.py`
* 凡是 `learning_status = 'eligible'`、`feature_json IS NOT NULL` 或在 `behavior_event_annotations` 中已有人工標註身分的軌跡，**永久受到保護，絕不被淘汰**。

### 2. 寵物檔案頁新增「從近期攝影機軌跡截圖一鍵加入特徵」專區
* 檔案：`src/meowkeeper/app/web/api.py`, `src/meowkeeper/app/web/static/js/pets.js`, `src/meowkeeper/app/web/templates/settings.html`, `src/meowkeeper/app/web/static/css/monitor.css`
* 後端在 `api.py` 新增 `GET /api/v1/pets/track-candidates` 與 `POST /api/v1/pets/track-candidates/{event_id}/assign`。

### 3. 人工覆核即時提取特徵入庫（Web 與 Telegram 同步）
* 檔案：`src/meowkeeper/app/web/api.py`, `src/meowkeeper/core/notifications/telegram_feedback.py`, `src/meowkeeper/app/web/static/js/identity-detail.js`

### 4. 左側導覽列收合時完整釋放水平寬度空間
* 檔案：`src/meowkeeper/app/web/static/css/monitor.css`

### 5. AI API 模型選單功能修復與強化（`/settings/ai`）
* 檔案：`src/meowkeeper/app/vision_settings.py`, `src/meowkeeper/app/web/static/js/vision-settings.js`, `src/meowkeeper/app/web/templates/_vision_center.html`

### 6. 即時身分辨識歷史全為 unknown 根本原因修復（NEW）
* **問題**：`identity_inference_history` 為 0 筆，即時歷史記錄全為 unknown。
* **根因排查**：
  1. `IsolatedTracker` 回傳的來源識別碼固定為 `"isolated-inference"`，而管線內部是雜湊後的攝影機來源字串。在 `_apply_identity_resolution()` 中 `result.source_id != self._source_id` 成立，所有算完的身分推論結果皆被直接拋棄。
  2. 身分工作原與 5 FPS 的 YOLO 偵測共用同一個 `max_pending_tasks=2` 排程器，且逾時僅 3 秒；而 DINOv2 首次冷啟動約需 12 秒，造成低優先身分工作常態被擠壓拋棄。
* **修復**：
  * 在 `SessionTracker.update()` 中同步傳入真實 `source_id`，並在管線增加來源相容回退。
  * 為身分推論建立專用單工作者排程器 `meowkeeper-identity-inference`（佇列 4，逾時 10 秒），與 YOLO 解耦。
  * 在 `V02HostAnalyzer.create` 啟動背景執行緒預熱 DINOv2 模型（`identity._ready()`），消除冷啟動延遲。
  * `IdentityThresholds` 門檻優化為 3 影格 / 2 秒區間 / 70% 得票率。
  * 檔案：`src/meowkeeper/core/events/v02_host_analyzer.py`, `src/meowkeeper/core/events/v02_pipeline.py`, `src/meowkeeper/core/identity/base.py`, `src/meowkeeper/core/identity/temporal_identity.py`, `src/meowkeeper/core/inference/scheduler.py`

### 7. 寵物特徵庫與雲端 Vision API 語意文字特徵輔助回報（NEW）
* **實作**：
  * `PetProfile` 新增 `semantic_traits` 欄位與 `effective_traits()` 提取（嗨寶：橘白/橘貓/白底虎斑；Saki：棕色/虎斑/豹花/米克斯）。
  * `SemanticRequest` 攜帶 `pet_profiles` 資訊。
  * `GeminiVision` 在提示詞中自動注入已登錄寵物代號與特徵清單，要求回傳 `identity_candidate`（`pet_id`, `certainty`, `reason`），不得隨意捏造未登錄身分。
  * `normalize_result` 與 `normalize_storyboard_result` 安全解析身分候選；雲端身分僅作為「候選提示」，不覆蓋本機 DINO 辨識。
  * 檔案：`src/meowkeeper/core/identity/pet_registry.py`, `src/meowkeeper/core/event_semantics.py`, `src/meowkeeper/integrations/vision/gemini.py`, `src/meowkeeper/app/vision_settings.py`, `src/meowkeeper/app/web/api.py`

### 8. PTZ 實驗版環境健全化與 HUD 方向鍵恢復（NEW）
* **問題**：提示「目前使用官方 go2rtc；Xiaomi MISS PTZ 需要明確啟用隔離實驗版」，HUD 畫面上的虛擬方向鍵消失。
* **根因**：工作區先前缺少 `vendor` 目錄，導致 `ptz_backend_selected()` 回退為官方版判定，前端因 `ptz.enabled = false` 隱藏 `#preview-ptz-directions`。
* **修復**：
  * 建立 Windows Junction 將 `C:\ProgramData\MeowKeeperPlus\App\vendor` 鏈結至工作區 `vendor`。
  * 在 `tools/run_host_workspace_ui.py` 設定 `MEOWKEEPER_XIAOMI_MISS_PTZ_ENABLED=1`。
  * 實機測試 `ptz_status()` 順利取得 `status: available, ptz_available: true`，HUD 方向鍵正常顯示。
  * 檔案：`tools/run_host_workspace_ui.py`

---

## 測試驗證紀錄

在 SHRIMP-ASUS 上執行單元測試全數通過：
* `test_event_semantics.py`：通過
* `test_vision_settings.py`：31 項通過
* `test_v02_identity_behavior.py`：10 項通過
* `test_pet_enrollment_web.py`：10 項通過
* `test_v02_host_analyzer.py`：11 項通過
* `test_ptz_backend.py` / `test_xiaomi_miss_ptz.py`：9 項通過
