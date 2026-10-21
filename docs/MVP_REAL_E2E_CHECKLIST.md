# MVP 真實端到端驗收清單

日期：2026-08-04

目前狀態：`MVP_CODE_COMPLETE`／`MVP_REAL_E2E_PENDING`

本清單只記錄驗收狀態與安全摘要，不保存 Bot Token、Chat ID 原值、RTSP 帳密、完整
來源 URL、Cookie、signed media URL 或私人監控素材。

## 執行順序

1. 登入 Dashboard「設定 → Telegram」。閱讀頁面四步驟引導，使用「安全保存憑證」將
   Bot Token 與一個或多個 Chat ID 保存到 Windows Credential Manager；Credential Manager
   不可用時，程式使用 Windows DPAPI fallback。啟用通知後重新啟動 Host。
2. 按「傳送測試文字」，保存 HTTP status、Telegram `ok` 與 `message_id` 的安全摘要；
   不保存 Token 或完整回應。
3. 使用可連線的真實 RTSP 頁面，確認單一 FFmpeg producer 取得第一個影格；只保存 codec、
   解析度、FPS、連線階段與遮罩後錯誤。
4. 使用含貓 RTSP 與公開 YouTube 來源各自觸發 YOLO11n cat-only 偵測。
5. 對兩種來源分別確認 SQLite event、JPEG、MP4、Telegram 文字／圖片／影片，並記錄
   `ok=true`、`message_id`、事件 ID 與影片大小／時長等非秘密欄位。

## 驗收閘門

| 項目 | 目前狀態 | 證據要求 |
| --- | --- | --- |
| Dashboard Telegram 憑證保存 | `code_ready` | 前端欄位、Credential Manager／DPAPI 邊界、秘密不回填 |
| Telegram 文字 | `not_checked` | 真實 `ok=true` 與 `message_id` |
| Telegram JPEG | `not_checked` | 真實 `ok=true` 與 `message_id` |
| Telegram MP4 | `not_checked` | 真實 `ok=true` 與 `message_id` |
| 真實 RTSP 第一影格 | `not_checked` | 單一 producer、codec／解析度／FPS |
| 含貓 RTSP YOLO | `not_checked` | cat class 15、SQLite event、JPEG／MP4 |
| 公開 YouTube YOLO | `not_checked` | cat class 15、SQLite event、JPEG／MP4 |
| 兩來源完整通知閉環 | `not_checked` | 文字／圖片／影片均有 Telegram message_id |

在所有 `not_checked` 項目完成前，不宣稱 `MVP_REAL_E2E_COMPLETE`、家庭 RTSP 已驗證、
或真實 Telegram 已可用。Telegram inbound webhook 仍不屬於本 MVP 範圍。
