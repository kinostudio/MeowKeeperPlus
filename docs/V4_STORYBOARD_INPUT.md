# V4.3 多時間點貓框輸入（施工中）

## 已接線

`V02HostAnalyzer` 只有在 controller 注入 vision provider 後啟用 FrameEvidenceBuffer；沿用單次追蹤器產生的 crop_jpeg，沒有新模型、RTSP 或 FFmpeg。保留上限 64 個裁切、8 MB，每個裁切最多 256 KB。未啟用 API 時不新增這份快取。

接受事件後，EventSemanticService worker 的 input_preparer 最多等 2 秒收集后續觀察，與原影片組合／錄影平行；觸發端不等。對每條輸入軌跡選 4–6 個不同 JPEG、相隔至少 250 ms、至少 750 ms 視窗，從觸發前 4 秒至後 2 秒取證。這是待實機調整的起始上限，不宣稱對所有 FPS 最優。最多 8 個軌跡；多於限制或其中一隻缺證據，整次略過，不暗中遺漏貓。

每隻貓的 2×2／2×3 JPEG 具有 Track 編號、frame index、相對觸發秒數；外部服務只收到 track_01 等本次 alias，不收到 source URL、內部 track id、event id 或絕對家庭時間。圖片總上限 4 MB。Provider 仍需正式外送同意；本輪只用合成圖與 loopback HTTP。

## 證據驗證

每個輸入 alias 必須回覆且不得發明新的 alias。TAG 需合法 code、certainty、至少兩個有序合法 evidence indices；拒絕自行產生 start_ms/end_ms。保存的是粗略觀察點、time_origin=event_trigger、AI candidate，不是 MP4 PTS，也不是完整事件動作。無證據就空標籤或 insufficient_evidence。

輸出經本機白名單驗證、同一事件候選 revision 保存；自由敘述丟棄。原生影片 caption 使用「剛剛可能在…」，不把候選當人工確認。

## 尚未完成／不得宣稱

- source/track epoch 用於禁止跨來源／追蹤重建混圖；**尚不是物理 PTZ view epoch**。真實 PTZ 移動、自動對位、外部手動移動偵測與區域校正仍待接，不能宣稱 PTZ 感知完成。
- 事件 storyboard 的主要證據仍是逐貓裁切。另有選用、獨立同意的場景物件參考拼貼接線，只提供物件辨認上下文，不是事件當下證據；不得宣稱看過整段影片或單靠場景參考確認進食、喝水、貓砂使用。
- Yolo-only 模式沒有編號追蹤 crop，不能把它假裝成 V0.2 多軌跡 storyboard；完整前端模式／能力設定仍待接。
- 真人家庭辨識、隱私遮罩、慢 CPU/GPU 溫度背壓、策略與觀察窗口配置及真實 Provider/Telegram 驗收尚未完成。
