# Dashboard 介面品質檢查（2026-10-02）

本輪目標是改善現有 Web Dashboard 的資訊層級、手機版導覽與一般使用者提示；不變更攝影機、AI、Telegram 或安裝程序的後端契約。獎項水準是外部評審結果，以下僅回報可重現的本地檢查，不宣稱已獲獎或達到獎項評分。

## 已完成

- 共用導覽保留「守護日常／設定／維護」分組；目前頁有標示，手機版可收合、按 Escape 關閉並把鍵盤焦點還給開關。
- 總覽把「監控是否有新鮮影格與 AI FPS」、「來源是否有畫面」、「Telegram 是否啟用」列為主要資訊；程序啟動不等於監控成功。狀態 API 失效時不沿用看似成功的舊值；Tailscale／事件單獨失敗不再抹掉主要狀態。
- 來源、AI、環形快取、Telegram、Tailscale、小米接入等難懂選項加入可見的繁體中文用途、影響與限制。實驗性功能有明確標示；`0.45` 模型門檻不被誤稱為準確率。
- 事件、診斷、登入、初始設定與小米精靈補上操作順序、空白／失敗狀態。事件列表使用 DOM 文字節點顯示 API 資料，不直接插入 HTML。
- 共用樣式採本機靜態資源、鍵盤焦點與 reduced-motion 設定；不增加攝影機 producer 或額外輪詢。

## 可重現的驗收

- 隔離預覽：`python reports/ui-quality-20261002/preview_server.py`，開啟 `http://127.0.0.1:18080/dashboard`。預覽只提供 GET 與示例資料；頁頂明確標示不會連線攝影機或送出通知。
- 實際瀏覽器檢查 6 個頁面 × 360／390／768／1440 px，共 24 組；未見水平溢出。390 px 的 AI 設定與 1440 px 的總覽均做視覺複查。
- 檢查手機導覽開啟／Escape 關閉、Xiaomi 精靈對應的目前導覽項、來源與 Telegram 欄位說明、事件無資料畫面、區域非 JSON 錯誤提示及狀態 API 失效畫面。
- 對比度樣本：正文 10.94:1、說明 5.57:1、側欄 10.40:1、主要按鈕 6.77:1、頁首副標 7.85:1、眉標 5.36:1。這是抽樣，不取代完整 WCAG 審查。
- `python -m unittest tests.test_dashboard_experience tests.test_phase3_source_mvp -q`：12 個通過。先前相關四組 Web 測試有一次 42 個通過；另一次既有有限影片測試在 EOF 競態下偶發 `jpeg is None`，單獨重跑通過，不能稱為已修復。
- `ruff check --no-cache tests/test_dashboard_experience.py reports/ui-quality-20261002/preview_server.py` 通過；相關 JS `node --check` 通過；`git diff --check` 通過。`compileall` 寫入 repository `__pycache__` 時遭 Windows 拒絕存取，改將兩個新增 Python 檔的 bytecode 編至系統暫存目錄，通過。

## 尚未驗收／發布邊界

- 此輪未更新 Windows 可攜式發布包，也未升級 `C:\ProgramData\MeowKeeperPlus\App`。正式已安裝 Dashboard 仍須獨立建置、升級與實機回歸後，才算交付到使用者目前運行的版本。
- 本地設計預覽不證明真實 Xiaomi 影格、YouTube／RTSP 來源、Telegram 實際收件、Apple Watch 播放或長時間監控成功。
- 尚未進行完整螢幕閱讀器、跨瀏覽器、iPhone Safari 與外部可用性測試；也未接受 Awwwards／Webby／FWA 評審。

下一步應在不混入示例資料的已登入測試安裝版進行裝置實測，核對監控狀態與事件通知，再依真實回饋迭代。

## 2026-10-02 色系與 Tray 修訂

依使用者偏好，將前述綠色方向調整為米白背景、卡其側欄、暖棕頁首與陶土／沙金輔色。此節取代上方早期配色描述；版型、設定說明與資料契約不變。Tray 的正常／等待／需檢查圖示改為共用暖棕底的淺橄欖／沙金／陶土色，並重畫 16px 可辨識的掌印。新圖示以 `meowkeeper-earth-*.ico` 載入，舊檔名同步更新作相容資產；發布包必要檔案清單已納入新檔名。

隔離預覽實際顯示新色系；抽查正文 11.62:1、側欄連結 10.57:1、目前頁面 8.64:1、頁首 11.29:1、主要按鈕 7.55:1。這些是指定元素的對比值，不是完整無障礙認證。相關 Dashboard／Tray 測試 24 個通過，Ruff 與新增 Python 檔的語法編譯通過；正式已安裝版仍未升級，本輪沒有重啟現有 Tray 或 Host。
