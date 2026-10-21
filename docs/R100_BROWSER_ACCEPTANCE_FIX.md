# r100 瀏覽器實機驗收修正（2026-10-16）

## 正式 r99 的原始證據

以使用者已登入的 Edge、區網 Dashboard 驗收；沒有讀取 Cookie、Token 或小米密碼。

- 小米精靈：026c02 正在監控，subtype=2，1920×1080、HEVC、20 FPS。
- 首頁來源影格距今約0.1–0.3秒，單一來源連線；ONNX Runtime CUDA 分析約3.2–4.4 FPS。
- 首頁與即時預覽均空白。Console 明確顯示 shared-preview.js 的 `statsTimer is not defined`，於初始化 stopTimers 中中斷。
- 繼續檢查真實JPEG成功路徑，發現 `onFrame` 讀取不存在的 `probe`，會在影格已解碼後再次失敗。
- 區網HTTP頁面 `crypto.randomUUID` 是 undefined。首頁按「確認貓咪與動作」不開對話框；event-review 在 showModal 前呼叫此API。
- r99 包只選官方 go2rtc，前端明確顯示 MISS PTZ 未啟用。不是裝置不支援的證據。

## 最小修正

1. 移除共用JPEG控制器不存在的statsTimer清理；MSE播放器自身的statsTimer仍由自身管理。
2. 成功影格回呼使用實際image.naturalWidth／naturalHeight。
3. JPEG恢復後保留MSE降級原因；重新開始播放時重設統計視窗，休眠時間不計入即時FPS分母。
4. 共用request-id.js：支援原生randomUUID；區網HTTP使用crypto.getRandomValues產生UUIDv4。未使用Math.random，未降低登入／CSRF保護。
5. 人工回報與身分回報改用共用識別碼；base在各頁defer腳本前載入。
6. MSE緩衝清理只刪除仍存在且超過保留範圍的資料，避免remove→updateend→remove空範圍反覆循環，讓後續片段能繼續append。
7. MSE FPS改用requestVideoFrameCallback的presentedFrames差值；回呼可能被合併，直接數回呼次數會把真實多張呈現錯報為約1 FPS。第一個回呼僅作統計基準，避免初始偏高一張。
8. 小米精靈停止讀取已移除的runtime.latest_jpeg，改以source.preview_saved_at與Host checked_at判斷15秒內JPEG及有效連線。無需暴露本機路徑，亦不使用瀏覽器時鐘冒充Host新鮮度。

## 已執行驗收

- Node實際執行共用預覽控制器：初始化→解碼回呼及1920×1080尺寸→休眠不送請求→MSE不可用降級→分頁隱藏／恢復→close清理。
- Node模擬randomUUID缺失：1000個CSPRNG UUIDv4不重複，原生API與無可用隨機API情況亦驗證。
- 17項預覽／回報／影片相容性專項passed。
- 真實Edge、隔離合成JPEG：640×360、約4.7更新／秒；休眠與降級後恢復passed，console error空。
- 真實Edge、拋棄式V4.3資料庫：首頁「確認貓咪與動作」開啟回報對話框，H.264合成影片640×360完整播放0→5秒、readyState=4；沒有保存正式人工答案。區網缺randomUUID的分支另由Node實際執行測試驗證。
- 真實Edge、修正後原始碼＋現有正式go2rtc串流：兩個觀看端均解碼1920×1080 HEVC、currentTime持續前進、readyState=4、paused=false。
- 同台攝影機producer保持1；原RTSP consumer保持1；兩個MSE觀看端時WS consumer=2、總consumer=3；全部驗收頁關閉後WS=0、總consumer回到1。
- 沒有另外啟動攝影機ingest、FFmpeg或重新登入小米；隔離HTTP伺服器已停止。

## 尚未通過的界線

- 初次瀏覽器HUD約1 FPS是按回呼次數的舊統計；獨立讀到影片累積解碼計數持續增加。另確認超過25秒的緩衝清理有可重現循環。兩處已新增Node實際執行回歸；最終實機數值於下方補記，不能將來源FPS直接當播放FPS。
- 正式r99尚未套用本次原始碼；依使用者規則，由使用者自行升級新包。
- MISS PTZ需候選包包含既有驗證雜湊的獨立backend，保留官方binary；本輪沒有移動鏡頭或驗收位置恢復。
- Telegram真實語意通知／逐軌跡回報、MX250長測及整份長期目標不由本輪預覽驗收替代。
- 前輪sandbox查排程為空不可宣稱排程不存在。Aaron邊界本輪讀到Tray排程Running；Host健康與現有PID亦存在。SYSTEM Core排程的完整高權限驗收仍未完成。

## 發布狀態

候選包已完成：`dist/MeowKeeperPlus-portable-2026-10-16-r100`；20,644個manifest檔案，內附Python執行verify_portable退出0、status=passed、private_data_present=false。正式Host仍為r99，未自動安裝、重啟或觸發UAC。

- 最後一輪完整回歸：751 tests／223.498秒／OK；Ruff、compileall通過，紀錄`reports/r100-unittest-release.txt`。
- 小米精靈顯示修正後另跑35項Xiaomi／預覽／識別碼專項通過；Node實際執行精靈的原始render程式，驗證新鮮／過期／無JPEG／零連線四種狀態。此項晚於上述完整回歸，沒有把它冒充為另一輪全套結果。

- 本次應用文字掃描：src／tools／scripts／configs／docs／README中的Telegram Token、完整數字Xiaomi URL、GitHub Token樣式，命中檔案0。此範圍不含第三方runtime／模型二進位，不能稱為任意秘密的全面證明。
- r100候選使用`--include-experimental-models --enable-verified-ptz`；PTZ binary SHA-256已核對為既有固定值，官方binary保留。沒有執行正式安裝器或UAC。

## MSE 最終修正後的短程實機樣本

固定同一正式026c02來源，修正後在Edge顯示`1920×1080 · smooth`、呈現20.0 FPS：

| 分頁經過時間ms | 播放時間秒 | 總影片影格 | dropped | HUD |
| --- | --- | --- | --- | --- |
| 22154.5 | 19.639159 | 402 | 5 | 20.0 FPS |
| 55155.1 | 52.108154 | 1051 | 7 | 20.0 FPS |
| 90155.6 | 87.108649 | 1751 | 9 | 20.0 FPS |

兩樣本間總影格增加649，約19.67張／秒，新增掉幀2張；console error空。已跨過25秒緩衝清理門檻，影片時間持續前進。這是隔離Web播放器使用正式既有上游的真實短程證據，不是長時間硬體效能、遠端裝置、WebRTC或已安裝r100的證明。

第二個觀看端同時顯示20.0 FPS；此時producer=1、總consumer=3（原RTSP=1＋WS=2）。關閉兩頁後consumer=1、WS=0；本輪啟動的三種隔離伺服器均已停止。原正式Host／go2rtc持續運作。

## 最終交付界線

- 7個修正後關鍵JS／template在候選包與repository的SHA-256一致。
- 官方v1.9.14 binary為`923D57252E8139A69C52E4ACC1E399A640244A8EF457FD9B7267A25847D68F8C`，仍獨立保留；獨立PTZ binary為`3F64D4C5144350DC816ABB4DE41BD686E248A9528EB2825A00CD8FC65EFEA0F3`，backend選擇為ptz-feedback。
- 本輪短程1080p／20 FPS與雙端單一producer已通過隔離實機；長期穩定、真實PTZ移動／位置恢復、真實Telegram回條及r100正式安裝後驗收仍待完成，不能標記全部長期目標完成。
- 本段為建置完成後repository補記，未回寫已產生manifest的候選包。
