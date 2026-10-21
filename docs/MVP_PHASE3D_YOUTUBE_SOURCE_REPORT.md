# Phase 3D：YouTube source adapter MVP

## 範圍

本階段只加入公開 YouTube 影片／直播頁面的來源 adapter，不修改 Xiaomi Cloud、
go2rtc Xiaomi、`miss_get_vendor`、ONVIF 或模型。第一版不支援 Cookie、OAuth、
私人／會員／年齡限制、DRM、下載存檔、播放清單或直播從頭播放。

## 實作契約

- source type：`youtube`。
- 設定只保存原始 `youtube.com`／`youtu.be` 頁面 URL、`max_height`、video/audio 開關。
- 固定 yt-dlp：`2026.7.4`（Windows CLI 顯示 `2026.07.04` 時以數字版本正規化比對）。
- yt-dlp JavaScript challenge runtime：`yt-dlp-ejs==0.8.0` 加 Node／Deno／Bun 其中一個。
- FFmpeg／FFprobe 優先使用 `vendor/ffmpeg/bin`；終端使用者不需另行安裝。
- 發布包應將 Node／Deno／Bun 放在 `vendor/javascript`；開發機才允許退回 PATH runtime。
- `max_height` 只接受 360、480、720、1080；預設 720。
- 預設 `video=true`、`audio=false`，MX250 分析輸出為 640 寬、5 FPS JPEG。
- 解析結果只保留安全 metadata：`video_id`、`title`、`is_live`、`live_status`、
  `selected_height`、`video_codec`、`audio_codec`。
- 簽名媒體 URL 只在 resolver 程序記憶體中存在，永不進入 Config、SQLite、日誌或
  狀態 JSON。部分 Google video host 會拒絕 FFmpeg 的 open-ended HTTP request；固定版
  yt-dlp 因此以 1 MiB finite Range chunks 輸出至匿名 pipe，再由唯一 FFmpeg 程序解碼並
  分流 JPEG／壓縮 ring。這不是下載存檔，不建立第二份持久媒體。
- 同一個來源 hash 同時最多一個 resolver 與一個 FFmpeg producer。
- FFmpeg EOF、403、manifest 失效或非正常退出後，先關閉程序，再重新執行 yt-dlp；退避為
  3、6、12、24、60 秒。正常結束的一般影片進入 `ended`，公開直播則進入重連狀態。
- 狀態：`resolving`、`connecting`、`streaming`、`waiting`、`ended`、`reconnecting`、
  `unsupported`、`failed`。

## Runtime 探針

```powershell
python tools\youtube_probe.py
```

探針只檢查 yt-dlp 版本、內含 FFmpeg／FFprobe、JavaScript runtime 與 `yt-dlp-ejs`，不
解析使用者 URL，也不建立外部串流。

## 自動化驗證

`tests/test_youtube_adapter.py` 驗證設定不保存 signed URL、metadata 解析、固定 yt-dlp
format selector、finite chunk pipe、私人／年齡限制分類、單一 producer lease及結束後
重新解析與退避。Phase 2 測試另驗證 yt-dlp upstream 只啟動一次、FFmpeg 使用 `pipe:0`，
且 command 不含 signed URL。
本輪 `unittest discover` 共 148 項通過，`compileall` 通過；YouTube 相關及本次修改檔案的
Ruff 檢查通過。完整 repository Ruff 仍有既有 `tools/phase3c5_xiaomi_trace.py` 的 lint
問題，本階段未改動該 Xiaomi 診斷工具。

## 真實公開素材驗證

本次使用公開 Big Buck Bunny 測試影片：

```text
https://www.youtube.com/watch?v=aqz-KE-bpKQ
```

已驗證：

- yt-dlp metadata 成功：video id、標題、`not_live`、360 高度、AV1 video codec。
- FFmpeg 成功取得第一張真實 JPEG：SOI `FFD8`、EOI `FFD9`，約 3 KB。
- `run_monitor.py --source-type youtube --analyzer noop --max-frames 1` 正常結束。
- console／adapter status snapshot 未包含 `googlevideo` signed media URL。
- `tools/youtube_probe.py` 回報 yt-dlp `2026.07.04`（符合固定 `2026.7.4`）、
  yt-dlp-ejs `0.8.0`、FFmpeg／FFprobe 與 JavaScript runtime 均可用。
- 本次 runtime／設定持久化秘密樣式掃描為 0 命中；測試中的 signed URL 僅為合成遮罩測試資料。

家庭 YouTube 頻道、直播長時間重連、MX250 實機負載與外部 Telegram 仍需獨立驗收；本報告
不把一次公開影片 smoke test 宣稱為 12～24 小時產品驗收。

## 2026-09-21 可攜版修正驗證

- 可攜式 embedded Python 已包含固定版 `runtime/python/Scripts/yt-dlp.exe`；建置器會
  執行 `--version`，缺少或版本不符時拒絕建置。
- 目前設定的公開影片 `mtdDmOxNfLI` 可解析為 720p H.264；FFmpeg 直接讀 signed MP4
  確認會收到 HTTP 403，而 `yt-dlp --http-chunk-size 1M` → pipe → FFmpeg 可取得真實 JPEG。
- 封裝 runtime 執行 `run_monitor.py --analyzer yolo --max-frames 1` 成功取得第一影格、
  完成 CPU 推論且 Telegram 未啟用；事件數為 0。
- 單一 ingest 的 10-frame noop smoke 成功產生 1 個 MPEG-TS 分段（262,144 bytes），
  結束後無遺留 bundled yt-dlp／FFmpeg 程序。此證據驗證來源、影格與 ring 接線，
  不代表影片內容含貓或 Telegram 真實傳送完成。
- 重建後的最終可攜包含 18,651 個檔案，模型 SHA-256 為
  `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`。
- 當前完整 unittest 為 189 項通過；全 repository Ruff 與 `compileall` 通過。

## 2026-09-23 YouTube 貓咪事件影片實測

- 以公開頁面 video id `I7ZRseJlZS4` 進行 bounded smoke，解析 metadata 為 360p VP9、無音訊；初次測試發現 VP9 直接放入 MPEG-TS 只產生 data stream，YOLO 雖偵測到貓且保存 JPEG／SQLite，MP4 組裝失敗。
- 已在 `FfmpegMediaIngest` 加入已知 MPEG-TS codec 判斷：H.264／H.265 繼續 stream-copy，其餘已知不相容 codec 使用 H.264 低負載快取轉碼，限制 640 寬／最高 5 FPS 並設定一秒 GOP。
- 修正後 repository 版以 100 影格、YOLO11n CPU 完成真實推論：偵測 1 隻貓、confidence 0.57；建立 SQLite 事件及可讀 MP4（H.264、640×360、5 FPS、10 秒、447,760 bytes）。Signed URL 標記在輸出檔 0 命中，Telegram disabled。
- 只證明這支公開一般影片的來源→YOLO→SQLite→MP4 路徑；不代表直播、家庭 RTSP、真實 Telegram 或 MX250 已驗證。當時此修正版尚未進入目前使用者安裝包，後續狀態見下一節。

## 2026-09-23 r2 可攜包與安裝版複驗

- r2 可攜包真實 YouTube bounded smoke：同一公開影片、100 影格、YOLO11n CPU 偵測到 1 隻貓（confidence 0.57），SQLite `clip_status=completed`。FFprobe 驗證 MP4 為 H.264、640×360、5 FPS、7 秒、363,882 bytes；因分段／關鍵幀邊界，時長與 repository smoke 的 10 秒不同，兩者均為可讀事件影片。
- Telegram 明確停用且 attempts=0；解析出的 signed media URL 沒有保存至輸出，輸出掃描 0 命中；清理後零位元組 ring media segment 為 0。
- r2 manifest 驗證通過，18,657 個檔案，預設模型 SHA-256 為 `0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1`，私人資料檢查為 false。r2 已升級至目前使用者安裝目錄；安裝後 Dashboard health=200、Host=1、Tray=1、go2rtc=0，正式 Python 來自 embedded runtime。
- 管理員初始設定尚未完成（auth `configured=false`），因此這不是 Telegram 文字／圖片／影片真實傳送驗收。此節也不證明 YouTube 直播、RTSP、MX250 或 12–24 小時穩定性。
- 最終回歸為 200 個 unittest、全 repository Ruff 與 `compileall` 通過。專案狀態仍是 `MVP_CODE_COMPLETE / MVP_REAL_E2E_PENDING`。

## 2026-09-23 r2 現行公開直播影格與環形快取 smoke

- 使用 [Caring for Cats 官方 24 小時 Kitty Cam 頁面](https://caringforcatsmn.org/livestream/) 目前嵌入的公開直播，安裝版固定 yt-dlp 解析為 `is_live=true`、360p H.264／AAC。先前搜尋到的另一個 24/7 頁面已變成不可用錄影；改用官方頁面的現行直播後解析成功。
- 安裝版 embedded Python／FFmpeg／FFprobe、Node 與 `yt-dlp-ejs` 均通過 runtime probe；YOLO backend=`CPU`。第一次 bounded run 以 `--max-frames 8` 正常完成 8 張真實影格推論，符合事件策略的貓事件為 0。
- 第二次啟用 `--event-video`，以 `--max-frames 30` 完成實際直播→影格分析→壓縮 ring 流程；Telegram 沒有啟用。ring 留下 3 個非空 MPEG-TS 分段（175,780／176,720／175,216 bytes）；FFprobe 驗證 H.264 640×360、約 5.016 秒；SQLite `media_events` 為 0 筆，因該取樣窗未觸發貓咪事件。
- 這證明目前安裝包可取得現行公開直播影格並寫入壓縮環形快取，不證明直播長時間斷線恢復、該直播畫面出現貓時的正例偵測、事件 MP4 或 Telegram 外送。這些仍需分別驗收。
