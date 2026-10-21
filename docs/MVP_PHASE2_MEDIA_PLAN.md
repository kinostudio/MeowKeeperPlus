# MVP Phase 2 媒體資料流計畫

## 目的與目前盤點

本階段只處理壓縮環形影音、事件 MP4、`/live` 片段服務及 Telegram 影片佇列；不新增模型、GUI、ONVIF、PTZ、Re-ID 或行為種類。

目前 repository 已有：

- `FfmpegFrameSource`：以單一 FFmpeg 輸入產生低解析度 JPEG 影格供預覽與 AI，具有限網路重連。
- `CompressedRingBuffer`：記憶體內壓縮資料的契約測試類別，但尚未連接 FFmpeg 分段檔案、事件組片或磁碟輪替。
- `NotificationDispatcher`：有界背景通知佇列，目前 Telegram 客戶端只有文字與圖片。
- `SQLiteStore`：WAL、busy timeout、有限重試及可重跑 schema migration，尚無媒體事件／影片狀態欄位。
- `RetentionPolicy`／磁碟檢查：可供事件檔案保留期限與容量管理使用，但尚未接入環形分段引用計數。
- 現有 `run_monitor.py` 只把分析事件送成圖片，未產生事件 MP4。

本計畫不把 OpenCV 解碼後的 BGR 或 JPEG 影格當成長時間錄影快取；影格只留給即時 AI，壓縮影音另走分段檔案路徑。

## 單一來源與本地分流

產品原則是攝影機只建立必要的單一 RTSP 554 來源連線。媒體資料流如下：

```text
來源（本地檔案／YouTube 解析後串流／未來 RTSP 554）
        │
        ▼
單一本地媒體 ingest 邊界
        ├─ 低 FPS、640 級 JPEG 影格 → 預覽／YOLO／事件觸發
        └─ 原始或低負載壓縮分段 → 環形快取／事件組片／/live
```

現有 `FfmpegFrameSource` 仍保留作為相容的 JPEG 來源介面；本階段的 `FfmpegMediaIngest`
實際以同一個 FFmpeg 輸入程序輸出 JPEG 與壓縮分段，故 RTSP、YouTube 與本地影片不需要
第二條來源連線。`SegmentRecorder` 仍可作為獨立 watchdog 工具，但對 `rtsp://`／`rtsps://`
若沒有本地 ingest 中繼網址會拒絕啟動，避免誤用它建立第二條攝影機連線；正式 `--event-video`
路徑使用 `FfmpegMediaIngest`，不猜測 D-Link RTSP 路徑。

## 壓縮環形快取

- 優先使用來源的 H.264／H.265 stream copy；無法 copy 時才使用明確低負載的兼容轉碼。
- 以約 1 秒 MPEG-TS 分段保存；每段具檔案索引、時間、大小、引用計數及目前狀態。
- 預設保留 60 秒，設定範圍 10～120 秒；另有最大目錄容量，超限時先刪除未被引用的最舊分段。
- 事件組片期間分段加引用計數，引用未釋放前不可輪替刪除。
- FFmpeg 分段程序有 watchdog、有限指數退避與正常關閉清理；暫存目錄不可寫或磁碟低於門檻時保留健康警告而停止新增一般事件影片。
- 分段命名含 session 前綴，避免重啟後覆蓋舊檔；啟動與停止時清理孤兒及超限檔案。

設定集中於 `media.ring_buffer`：`enabled`、`segment_seconds`、`retention_seconds`、`storage_mode`、`temp_directory`、`maximum_size_mb`、`prefer_stream_copy`。`live_pre_seconds` 與事件前／後錄秒數也集中管理，且前錄不得超過環形保留時間。

## 事件觸發與 MP4 流程

1. 分析管線產生已符合 cat-only 與冷卻策略的事件，建立 `event_id` 與 SQLite 媒體事件列；身分可為 `unknown`。
2. 以觸發時間鎖定前錄分段並增加引用；預設一般事件前 5 秒、後 10 秒，`/live` 前 5 秒、後 0 秒。
3. 後錄期間只等待分段邊界，不阻塞影格分析、快取或 Telegram。
4. 收集完整分段後先用 concat demuxer + `-c copy` 組合 MP4；允許因關鍵幀而略長於請求範圍。
5. 以 `ffprobe` 驗證容器可讀、存在影像串流、時長大致合理；copy 失敗才使用低負載 H.264/AAC 兼容轉碼。
6. 組片成功後更新影片路徑、大小、實際時長與狀態，再把傳送工作放入獨立有界 Telegram 佇列。
7. 組片失敗仍保存錯誤、事件資料及至少一張觸發影格截圖；不能因影片失敗丟棄事件。
8. Telegram 失敗只更新 `telegram_status`／重試資訊，保留本地影片與 SQLite 紀錄。

## 程序、執行緒與佇列邊界

- FFmpeg 分段器：一個受 watchdog 管理的子程序；分段索引與輪替由有界的監看執行緒完成。
- AI 影格：沿用 latest-frame-wins／有界分析佇列；不把影片組片放進 AI 佇列。
- Clip assembly：固定大小的有界佇列與單一（或設定上限的少量）工作者；重疊事件共用分段，不建立無限執行緒。
- Telegram：獨立有界佇列、逾時、有限重試與退避；未明確啟用外部傳送時工作數為零。
- SQLite：只在事件建立、影片完成或 Telegram 狀態改變時寫入，不逐影格寫入。

## 故障與降級策略

FFmpeg 意外退出時以有限指數退避重啟，避免快速重啟迴圈；來源中斷不會刪除既有有效分段。組片、Telegram、SQLite 暫時錯誤各自隔離，並以繁體中文健康訊息記錄。磁碟低於門檻時停止一般事件影片與新分段，但保留狀態／錯誤紀錄及必要的既有事件。正常關閉時先停止新事件、等待有界佇列、釋放分段引用，再終止 FFmpeg 子程序。

## 本階段驗證界線

自動測試與合法本地素材會驗證分段輪替、引用保護、無音訊／有音訊組片、copy→轉碼降級、`/live`、Telegram disabled、重疊事件及 watchdog。YouTube、D-Link RTSP、真實 Telegram Chat ID 及 MX250 24H 穩定性仍需實機驗證，不得由本地測試推定完成。
