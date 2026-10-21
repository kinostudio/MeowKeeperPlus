# r87 Tray 操作與完整關閉

## 修正

- 固定右鍵選單不再每兩秒 update_menu；狀態圖示只在分類改變時更新。
- 選單 callback 不等待 API、檔案或確認對話框：立即派至單一背景工作，重複操作不建立無限執行緒。
- 新增「完整關閉喵喵守護者+」，先顯示停止 24H 監控的確認。它不同於「停止背景核心」（後者只停監控，保留 Dashboard）。
- 獨立 helper 先嘗試有驗證的 Host shutdown API，再清理由此安裝完整路徑／入口識別的 Host、媒體／AI、Tray；重新核對建立時間與命令列以避免 PID 重用。未刪除資料、設定、憑證或排程。
- 沒有可用 API Token 時走 OS 程序關閉，不繞過登入。有驗證的 shutdown API 成功後會先給 Host 最多 30 秒安全退出。Windows terminate 屬強制 fallback，最後未完成的快取或通知可能中斷；不宣稱等同 graceful shutdown。
- SYSTEM 安裝版由使用者選擇此功能後才可能顯示 UAC；取消授權或權限不足不回報成功。只 End XML 識別到此 App 的排程，排程仍保留，後續手動啟動／下次開機可恢復。
- 範圍只有目前應用根目錄，不跨目錄關閉其他可攜包、獨立 relay、Python 或 FFmpeg。

## 操作回應

- 「目前狀態」查詢後顯示摘要視窗：Dashboard 可連線、背景監控、影音來源、AI 後端及 Telegram。可連線不等於正在取流；未知值明確標為尚未確認。通知不包含來源 URL 或診斷全文。Tray 沒有有效 API 授權時無法取得完整監控狀態，不能只凭健康檢查宣稱監控成功。
- 啟動／停止／重啟顯示處理中通知，API 後重新查詢狀態並顯示結果視窗；不把「已送出要求」誤當成影格驗證成功。
- 截圖／影片明確標示為開啟即時畫面，再由頁面保存；Tray 不宣稱已產生檔案。
- 開啟 Dashboard、小米設定、日誌、事件資料夾均有通知；瀏覽器未開啟或權限失敗顯示錯誤視窗。
- 診斷匯出完成後顯示實際路徑，SYSTEM 安裝使用 Data/reports，不寫入 App。完整關閉先確認，再交給獨立 helper；UAC 取消或清理失敗由 helper 告警。
- 通知使用 Windows Tray 通知，不會發 Telegram；Windows 勿擾可能隱藏通知，因此狀態、核心操作與錯誤另有對話框。對話框位於背景工作，不阻塞原生選單；尚未關閉時重複操作只通知等待。

## 驗證結果

- passed：全部 518 tests，116.488 秒；Ruff；compileall。Tray 相關 8 tests 包含 auth／CSRF、確認、程序範圍、PID 重用、慢 callback 不阻塞、單一工作上限與狀態摘要秘密白名單。
- passed：r87 建置與完整 manifest 驗證，19,299 檔；private_data_present=false。六個本輪 runtime 關鍵檔案與來源 SHA-256 相同。
- passed：本輪 Tray 四個修改檔案的 Bot Token、含帳密 RTSP、含帳號 Xiaomi URL 模式掃描 0 命中；不是整個歷史 repository 的秘密掃描證明。
- passed：真實 Windows shell 執行 r87 Dashboard.cmd，以臨時資料目錄、18789 Dashboard／21989 API／28559 RTSP 設定隔離，未啟動攝影機與外部通知；healthz HTTP 200，Host PID 6448，embedded Python 路徑在 r87。測試 FFmpeg PID 32644，來源是本地 lavfi 色塊。
- passed：r87 close_application.py 結束碼 0，該包程序從 Host／FFmpeg 變成空清單，Dashboard port 釋放。只關閉本輪隔離包，沒有操作正式安裝版。
- not_checked：本輪正式 Tray 啟動沿用已有單一實例，啟動程序 exit=0，但兩秒時沒有新的 Tray 活動程序；不能拿此結果宣稱新版原生選單、通知外觀或右鍵卡頓已實機通過。非阻塞 callback 有自動測試，需升級後由使用者實際點選驗收。
- not_checked：SYSTEM UAC、Windows 勿擾下實際通知展示、正式帶攝影機／獨立 AI 工作的完整關閉與 Windows 重開機。尚未替使用者升級正式 Host。

驗證命令：`python -m unittest discover -s tests -q`、`python -m ruff check src tools tests`、`python -m compileall -q src tools tests`、`python tools/verify_portable.py --root dist/MeowKeeperPlus-portable-2026-10-12-r87`、`python reports/r87_tray_smoke.py`。
