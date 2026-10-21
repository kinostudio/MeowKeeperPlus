# V4.3 驗收覆核（2026-10-17）

## 結論

**V4.3 尚未完成全部驗收。** 本次目前工作樹的程式／隔離測試通過；真實視覺 API、實際 Telegram 收件與按鈕落庫，以及正式發布包執行仍是 `not_checked`。不得把程式測試等同於真實端到端完成。

## 本次驗證

| 檢查 | 結果 | 範圍 |
| --- | --- | --- |
| `python -m unittest discover -s tests -v` | **passed**：859 tests，272.724 秒 | 全 repository 測試；含替身／合成媒體，不是實際雲端或 Telegram 收件 |
| `ruff check src tools tests` | **passed** | Python 靜態檢查 |
| `python -m compileall -q src tools tests` | **passed** | Python 編譯檢查 |
| `tests/js/*.cjs` | **passed**：6 個瀏覽器 runtime 測試 | UI 行為替身，不是家庭使用者驗收 |
| `node --check`：`static/js` 根目錄 JS | **passed** | 語法檢查；未包含遞迴子目錄 |
| 本輪追加 `tests.test_telegram_feedback`、`tests.test_event_semantics`、`tests.test_track_tag_feedback`、`tests.test_web_event_review` | **passed**：60 tests，22.738 秒 | 含隔離閉環：AI candidate → 原生影片 caption／fake message_id → 逐軌跡 callback → SQLite 歷史，以及語意 API 延遲時影片組裝仍可併行；仍非真實 Telegram/API |

## 執行環境邊界

本次唯讀檢查所在身分為 `aero15\\codexsandboxonline`。該環境的 `127.0.0.1:8000/healthz` 無法連線，且沒有 8000／1984／8554 listener；對先前提供的 `192.168.31.200:8000/healthz` 請求逾時。嘗試使用 Windows Computer Use 查看已登入瀏覽器時，工具因無法可靠確認目前 URL 而中止；依工具要求沒有繼續操作。這只代表隔離 sandbox 無法接觸該 Host，**不代表 Aaron 的互動式 Windows 主機已停止或未安裝**。正式 Host 版本與狀態仍是 `not_checked`。

## V4.3 條件對照

| 條件 | 目前證據 | 判定 |
| --- | --- | --- |
| 沿用既有單一 ingest／既有事件影片 | 媒體與事件回歸測試通過 | 程式接線 `passed`；實機 producer 數未在本次重驗 |
| 代表圖交由可選視覺 API，背景併行工作、有界佇列與逾時 | Provider、semantic worker、設定與 request guard 測試通過 | 程式接線 `passed`；真實供應商請求 `not_checked` |
| 影片可送出後才等待 AI；預設 5 秒、範圍 0–10 秒、硬上限 10 秒；逾時回退本機 caption | `test_wait_happens_after_conversion_and_send_video_keeps_caption`、`test_zero_five_ten_grace_only_begins_after_video_is_sendable`、逾時與 429 回退測試通過 | 程式時序 `passed` |
| 保留原生影片通知，不新增「查看影片」主 CTA | 影片組裝／sendVideo 與 caption 測試通過 | 程式接線 `passed`；本次沒有實際 Telegram `message_id` |
| Telegram 逐 Track 身分單選、動作多選，確認後追加回報歷史 | staged identity／多選／確認、雙軌跡、冪等及 Web review 測試通過；新增 `test_v43_semantic_candidate_native_video_and_track_feedback_close_loop` 串起 candidate、影片回條、軌跡引用與 SQLite 歷史 | 程式接線 `passed`；真實 callback 點擊與正式 SQLite 落庫 `not_checked` |
| Dashboard 逐段候選覆核 | 接受／駁回／修訂／撤回及有效投影測試通過 | 程式接線 `passed`；真實家庭片段操作 `not_checked` |

## 尚未通過的外部驗收

1. 使用真實、已同意外送的事件代表圖呼叫實際視覺 API，取得並覆核候選 TAG／動作摘要：**not_checked**。雖已獲得本次測試授權，但隔離環境無法連到運行 Host，故未取得已保存 Provider 設定，也未傳送影格。
2. 在指定 Windows Host 實際送出事件 MP4，確認 Telegram 回條含有效 `message_id`，收件端能播放影片：**not_checked**。沒有向 Telegram 發送影片或訊息。
3. 實際點擊每條軌跡的身分／動作按鈕，確認相應人工修訂寫入正式 SQLite 且不覆寫既有歷史：**not_checked**。沒有正式收件端可供點擊。
4. 對正式發布包重跑上述流程：**not_checked**。本次沒有建置、安裝、重啟 Host 或發送 Telegram 訊息；已授權的外部測試因 Host 不可達而未開始。

完成條件是 1–4 都有指定版本的真實證據；目前狀態應維持 `CODE_WIRING_ACCEPTED / REAL_E2E_PENDING`。
