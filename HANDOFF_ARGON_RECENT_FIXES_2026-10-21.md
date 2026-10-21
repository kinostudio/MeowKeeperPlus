# MeowKeeper+ 近期修正交接｜Antigravity / Argon

日期：2026-10-21
專案：[kinostudio/MeowKeeperPlus](https://github.com/kinostudio/MeowKeeperPlus)

## 交接目的

請接手近期 Dashboard 圖表與寵物參考照片顯示修正，並檢查 DINOv2 身分輸入是否也受到長寬比拉伸影響。此文件交接的是開發工作區狀態，不代表已建置、發布或安裝。

## 版本與工作區證據

- 本機分支：`codex/r110-candidate`
- 本機 `HEAD`：`a028de49d4cabf5e5344b8cec8a00a79d647b6cb`
- `dist/release-r111/MeowKeeperPlus-r111-windows-x64.zip` 存在；同步時應以此包與 SHRIMP 正式安裝 manifest 做第一方檔案基準核對。分支名稱不是發布版本證據。
- 工作區有大量未提交／未追蹤修改，含與本交接無關內容。先檢查實際 diff，保留所有既有修改；禁止 `git reset`、`git clean`、覆寫整個工作區或把整個分支推定為 r111。
- 本輪沒有建置、發布、推送 GitHub、安裝或修改正式 Host。

## 修正項目與目前狀態

### 1. 寵物檔案參考照片縮圖不可拉伸（優先）

目前已有局部 CSS 修改：`src/meowkeeper/app/web/static/css/redesign.css` 的 `.pet-photo img` 使用固定縮圖框與 `object-fit: contain`，預期在保留完整原圖的前提下留白，而不是把橫圖或直圖壓成正方形。`pets.js` 的 `width="128" height="128"` 是版面尺寸提示，不得因此對來源檔重新裁切或改寫。

**尚未完成的驗收：**在真實寵物檔案頁以橫式、直式、正方形照片確認比例正確、主體完整、容器對齊；檢查窄版、載入中、破圖及鍵盤可操作。此 CSS 尚未經本輪瀏覽器驗收，也未進入發布包。

### 2. DINOv2 輸入長寬比（獨立檢查，不可和縮圖修正混為一談）

`src/meowkeeper/core/identity/dinov2_embedder.py` 目前的前處理直接呼叫 `cv2.resize(image, (518, 518), ...)`。若來源 crop 非正方形，這會把影像內容幾何拉伸。縮圖 CSS 修正不會改變模型輸入，因此不能據此宣稱 AI 身分辨識也已修好。

請先追查登錄參考照與即時 crop 是否共用此 embedder／相同前處理，再設計一致的保留長寬比方案。需避免未經驗證的中心裁切遺失耳朵、尾巴或身體；記錄 padding／裁切規則、正規化與輸出尺寸。若改變 embedding 前處理，須加 preprocessing/version 標記，讓舊 embedding 安全失效並可重建，且不要靜默混比新舊向量。

**回歸與驗收：**橫式、直式、方形、多種貓咪 bbox；同一張圖走參考與即時路徑必須完全一致；檢查 tensor shape、向量有限值／正規化、未知與相似度結果。比較舊／新 embedding 的身分效果；沒有家庭實拍證據前仍標為 experimental，不宣稱準確率提升。

### 3. 資源趨勢圖與首頁互動一致

工作區已有共用圖表 renderer `src/meowkeeper/app/web/static/js/dashboard-resource-charts.js`，趨勢頁 `src/meowkeeper/app/web/static/js/resource-history.js` 與 `src/meowkeeper/app/web/templates/analytics.html` 嘗試共用首頁視覺與互動：游標／觸控選點、時間與多序列 tooltip、鍵盤左右／Home／End、缺值留白。

本輪已執行兩個 JS 檔案的 `node --check`，以及 `git diff --check`，退出碼為 0。**尚未**在瀏覽器實際驗證首頁與趨勢頁互動、不同期間、窄版、觸控、鍵盤及缺值顯示；也未建置或發布。請確認共用 renderer 的載入時序、時間軸期間、資料單位與空值語義，避免把缺測畫成 0。

### 4. CPU 溫度卡片硬體型號與來源提示

近期 UI 要求：卡片主要顯示 CPU 硬體型號；將「由 LibreHardwareMonitor 監控」放在 hover/focus 可讀的標籤提示。請檢查目前 API 是否穩定提供硬體型號與 provider，再確認首頁卡片的文案、鍵盤焦點及觸控可讀性。未核實資料來源時不可把 LibreHardwareMonitor 寫成事實；溫度缺值不可補零。

## 本次檢查結果

| 檢查 | 結果 | 範圍 |
|---|---|---|
| `node --check dashboard-resource-charts.js` | passed | JavaScript 語法 |
| `node --check resource-history.js` | passed | JavaScript 語法 |
| `git diff --check` | passed | whitespace 檢查；Git 另提示部分既有檔案將 LF 轉 CRLF |
| 寵物照片實際瀏覽器顯示 | not_checked | 尚未實機／瀏覽器驗收 |
| 圖表滑鼠、觸控、鍵盤與響應式 | not_checked | 尚未瀏覽器驗收 |
| DINOv2 長寬比修正與身分品質 | not_checked | 尚未實作／驗收 |
| 建置包、安裝 Host、家庭實拍準確率 | not_checked | 未執行 |

## 執行要求

1. 先保留並檢視目前工作區 diff，再確認 r111 發布包的第一方檔案基準；不要以 branch 名或舊報告代替核對。
2. 完成縮圖與 Dashboard 瀏覽器驗收；修正 DINOv2 前處理時，新增單元測試及 embedding version／遷移保護。
3. 對圖表執行前端語法、現有測試與瀏覽器操作驗收，記錄 `passed / failed / not_checked`。
4. 不讀取、列印或同步 Token、帳密、私鑰、DPAPI、攝影機 URL／DID、私人實機影片、`data/`、模型或發布包。
5. 除非另有明確要求，不建置、不發布、不安裝、不重啟正式 Host；本次 Argon 同步只傳工作區安全白名單檔案。

參考規範：[UI 品質標準](UI_QUALITY_STANDARD.md)、[身分／行為實驗計畫](V0.2_IDENTITY_BEHAVIOR_PLAN.md)、[AI 影像身分比對](AI_PHOTO_IDENTITY_COMPARISON.md)、[防重犯手冊](LESSONS_LEARNED.md)。
