# r90 Windows Tray 原生選單修正

日期：2026-10-12。使用者回報 r87 後仍有 hover 卡頓；不能沿用上一版單元測試宣稱問題已解决。

## 本輪程式證據與修正

檢查目前 pystray Windows backend：SetForegroundWindow 使用 `_hwnd`，TrackPopupMenuEx 卻使用另一個 `_menu_hwnd`，返回後沒有 WM_NULL。另有背景 refresh／通知直接呼叫 Shell 圖示 API。這些是可確認的程式風險；沒有取得使用者筆電卡住時的 thread dump，不宣稱是唯一根因。

新增應用內 adapter，不修改 pystray 安裝檔：

- 前景視窗與 popup owner 都使用同一 `_hwnd`，選單返回時補 WM_NULL。
- native loop 處理狀態圖示／通知；背景只 PostMessage，不在選單 callback 做 Shell／網路等待。
- popup 開啟時延後圖示、通知與 menu rebuild；關閉後套用最新狀態，通知緩衝最多 8 筆。
- 保留既有有界 action worker、完整關閉、登入與程序範圍，不停止 Host 或攝影機。

Microsoft 原生契約：[TrackPopupMenu remarks](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-trackpopupmenu)。

## 驗收邊界

自動測試驗證 owner 相同、WM_NULL、選單期間不更新、通知有界、取消／例外清理、慢操作不阻塞 callback。
隔離 Windows shell smoke 使用真正 pystray Win32 backend，只啟停測試 Tray，不啟動 Host、不接攝影機或 Telegram。

使用者 C701 筆電的原生 hover、快速重開、點擊外部／Esc 收合及 TeamViewer 環境仍需升級後人工驗收；native lifecycle 通過不是 hover 成功證據。正式 Host 不由代理升級。
