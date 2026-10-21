# AI 疊圖、CUDA 與發布隱私覆核（2026-10-19）

本文件是本機工程紀錄，不納入一般使用指南或 Windows 可攜包。

## 遠端實機疊圖截圖

- 實機：SHRIMP-ASUS 正式安裝版 r107；使用者已登入的 Dashboard 即時畫面。螢幕截圖顯示本機 go2rtc、1920×1080 節能 JPEG；「偵測框／身分候選／動作候選」三個開關均開啟。
- 唯讀讀回 `/api/v1/preview/status`：來源 `running`、單一連線、預覽 1920×1080／4.33 FPS、AI 輸入 640／0.6 FPS；最近 AI overlay 的 `detections` 是空陣列。影格年齡約 7.95 秒。畫面可見貓咪，但沒有框或候選，因此偵測框 **failed**；身分／動作候選因沒有軌跡 **not_checked**。截圖未另存含家庭影像檔案，僅在本輪 UI 中檢視。
- 另一次畫面快照的 AI 卡片約 0.2 FPS、溫度 50°C／瞬間 GPU 0%；JPEG 統計與 AI 分析使用不同時間窗，且瞬間 GPU 0% 不代表 CUDA 未運作。
- 實機效果判定：偵測框 **failed**；身分與動作候選 **not_checked**（沒有活動的正式軌跡）。本次截圖僅用來證明目前沒有作用中疊圖；未另存含家庭影像的圖片檔，也不進包。

## 身分候選差距門檻

- repository 預設 `minimum_margin` 從 `0.08` 降為 `0.05`，只影響最相近兩個寵物候選的差距閘門。
- `accepted_threshold=0.62`、`unknown_threshold=0.45`、時序確認與單貓家庭校準要求均未降低或移除。候選不是已確認身分；身分仍為 experimental。
- 隔離回歸：分數 `0.62234／0.54570`、差距 `0.07664` 可顯示第一候選但 `stable=false`；差距 `0.04` 仍為 `unknown/small_margin`；單一寵物仍 `calibration_required`。
- 此修改尚未建置或部署；r107 實機設定沒有被變更。

## CUDA 利用率與降階診斷驗收

- 同一台 MX250、相同模型／共享影格、640 px／3 FPS 的 60 秒隔離比較已有實測：阻塞式 GPU 採樣平均約 65.94 ms；背景快取採樣約 0.50 ms。吞吐為 2.9927 對 3.0008 FPS，未見實質 FPS 提升。
- 背景快取段 GPU 平均約 77.41%、峰值 99%、最高 58°C；模型節點證據為 CUDA 308、CPU 55。這表示 CUDA 確實承擔部分運算，但也超過 40% 長期負載目標，因此沒有強推 5 FPS。
- 此隔離測試沒有涵蓋正式 Host 的完整事件／身分／錄影／Telegram 管線；正式 Host 仍是 r107。正式畫面當下讀值 0% 是瞬間值，不覆蓋先前 60 秒實測。
- 判定：**部分通過**（CUDA 使用與採樣非阻塞有證據；效能提升未證實）。正式 Host 安靜模式根因、候選包、5 FPS 安全負載、長期與事件通知回歸仍 `not_checked`。

## 截圖／錄影提示

- 已改為預覽內的短暫 snackbar：截圖／影片各有簡短狀態，不提供手動關閉或複製按鈕、不攔截滑鼠，成功／失敗都會自動消失。
- repository 單元 runtime 測試、JS 語法檢查通過。完整 Edge 隔離瀏覽器視覺測試 `not_checked`（Playwright 模組不可用；本次隔離 fixture 使用的 18979 埠被其他程序占用且無法安全識別，因此未碰觸該程序）。

## 發布資料範圍

- Windows 可攜包文件使用白名單，只複製 `README.md` 與 `docs/PUBLIC_USER_GUIDE.md`；其他工程文件、實機資料與報告不會被整份複製到包內。
- 測試以含私有診斷內容的虛擬來源確認只有兩份一般說明被複製。
- 本輪未建置、未上傳 GitHub、未更新 SHRIMP。此報告和既有實機診斷仍留在本機工作樹。

## 測試結果

- `test_v02_identity_behavior.py`：10 項通過。
- `test_public_package_docs.py`：1 項通過。
- `test_monitor_ui.py`：1 項通過。
- `ruff check src tools tests`、`compileall -q src tools tests` 與相關 JS `node --check`：通過。
- 完整 `python -m unittest discover -s tests -v`：929 項通過，2 項略過（硬體／隔離媒體環境不可用）。
