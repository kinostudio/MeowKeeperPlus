# V4.3 三項實裝缺口：接線驗收（2026-10-16）

本次範圍是補齊上輪明列的三項實裝缺口。驗收分清楚 **repository 接線**、**隔離執行** 與 **正式部署／家庭實機**，不以其中一層代替另一層。

## 逐項完成條件

| 缺口 | 已完成的接線 | 本輪證據 | 判定 |
| --- | --- | --- | --- |
| 寵物／物件／鏡頭方向工作台 | 三個鍵盤可操作分頁；照片長方形／多邊形裁切→原圖另存→失效舊特徵→背景重建；物件多邊形與裁切相簿；參考圖綁目前裝置讀回位置與別名；指定位置後讀回＋移動後新影格幾何核對 | 瀏覽器實際畫框保存；DINOv2 真模型在合成圖建立1個特徵；SQLite四點polygon與綁定；HTTP測試拒絕錯來源、無讀回、過小裁切與移動前舊影格 | CODE_WIRING_ACCEPTED；物理PTZ尚未實測 |
| 工作取消／狀態與負載退讓 | Core callbacks→受保護取消API→同一service→SQLite取消旗標與終止原因；排隊可立即終止，執行中不假稱撤回；首頁工作介面；同一clip service組片／轉碼旗標與既有AdaptiveAnalyzer記憶體／溫度／負載訊號接入dispatch前檢查 | 瀏覽器取消排隊與執行中工作，SQLite兩筆cancelled/user_cancelled；測試證明僅一次provider呼叫；真實Core＋FFmpeg合成影片實跑 | CODE_WIRING_ACCEPTED |
| 首頁摘要／最新有效回報與說明 | 近24小時完整統計→首頁，與實際請求分母／人工有效性／p95分開；同event assessment整合媒體、tracks、identity evidence、jobs/revisions、候選覆核、有效Web片段、最新逐軌跡Telegram回報與投遞歷史；保存後重讀摘要，撤回不再有效 | 瀏覽器保存後立即顯示0–5秒修訂1；撤回後顯示無有效片段，保留修訂2及候選；取消／失敗job不遮掉之前有效候選；360/1440及console檢查 | CODE_WIRING_ACCEPTED |

這裡的「位置別名」是本機參考圖綁定，不宣稱建立攝影機韌體內的hardware preset。座標是原生協定值，不是角度。沒有實際讀回、裝置來源不符、畫面不一致或未取得移動後新影格時拒絕成功；不猜位置、不新增RTSP連線。

## 實作入口

- `app/web/templates/settings.html`、`registration-workspace.js`：三分頁；原pet/scene DOM ID保留，無JS時仍可閱讀各區。
- `pet-crop.js`、`core/identity/reference_crop.py`、`POST /api/v1/pets/{pet}/images/{image}/crop`：只保存人工明確圈選範圍，96px最低邊長；原圖在寵物目錄`reference_originals/`，仍會進完整資料備份，不會進乾淨程式包。
- `scene-registry.js`、`core/behavior/scene_views.py`：物件polygon、裁切縮圖、同來源跨視角邏輯物件、位置綁定；舊綁定仍保留，不靜默套到其他攝影機。
- `GET /api/v1/vision/runtime`、`POST /api/v1/events/{event}/analysis/{job}/cancel`：沿用登入／CSRF；不能取消別的event工作。
- `core/event_semantics.py`、`storage/semantic_jobs.py`：schema25；`cancel_requested`與`terminal_reason`可重跑遷移。已外送仍可能收費；回應後候選丟棄，原HTTP分母不消失。不把取消當作未外送。
- `RepositoryCoreController.semantic_pressure`由真正監控session綁給worker；`EventClipService._media_task`以counter/finally涵蓋組片與Telegram必要轉碼；`AdaptiveAnalyzer.cloud_pressure_reason`重用已採樣訊號，不新增硬體輪詢。
- `semantic-runtime.js`：首頁初次讀取及明確按更新時才查詢，不持續輪詢、不新增影像consumer；感測器未知不補造溫度。
- `core/events/web_review.py`：有效投影與完整append-only歷史分開；Web與Telegram來源分開，不偷偷合成同一人工真值。AI候選不等於人工標籤，不直接改模型權重。

## 接線測試與失敗案例

`tests/test_v43_completion_wiring.py`七項涵蓋：

1. Core callback→取消API→queued/running worker→SQLite，確認未多發API。
2. 真正AdaptiveAnalyzer訊號與Core media優先判斷→略過雲端，不停止本機分析。
3. 轉碼例外仍釋放media busy，避免永久阻擋可選分析。
4. 24→25保留工作、重跑兩次遷移。
5. 舊片段取代／撤回退出有效投影，歷史仍3份；取消job不覆蓋有效候選。
6. 圈選API→原圖備份→背景特徵重建→數量1；過小裁切拒絕。
7. 綁定需讀回＋背景，復位需裝置匹配、移動後讀回與新影格；錯攝影機不移動，舊影格不算成功。

此處硬體／雲端替身測試明確不算實體攝影機或外部Provider成功。

## 真正Windows程序與媒體實跑

命令：`.venv\Scripts\python.exe reports/v43_runtime_wiring_smoke.py`。

2026-10-16觀察：

```json
{"source":"local_synthetic_video","real_ffmpeg_alive_at_frame_sample":true,"ffmpeg_pid":6432,"source_connections":1,"first_frame_received":true,"service_constructed_by_real_monitor":true,"controller_pressure_callback_bound":true,"real_clip_media_task_blocks_optional_cloud":true,"runtime_api_worker_active":true,"camera_tested":false,"telegram_sent":false,"cloud_request_sent":false,"monitor_stopped":true,"owned_ffmpeg_exited":true}
```

第一輪20秒素材因本地檔案無`-re`快速解碼，在API讀取後FFmpeg已EOF；沒有把它寫成活動producer。改成120秒素材並在第一個影格時取樣，以上活動程序證據才通過。沒有修改正式ingest速度或重試真實攝影機。

## 隔離瀏覽器與保存證據

使用`reports/v43_dashboard_fixture.py`、localhost:18943、臨時SQLite及合成圖片／H.264影片。測試登入不是使用者帳密，沒有使用正式Telegram。

- 第一個fixture落庫：schema25、兩筆`cancelled/user_cancelled`、物件polygon長度4、已綁view1。
- 寵物裁切經真DINOv2回來：reference_image_count=1、embedding_count=1、archived_original_count=1。素材是色塊，不代表真實貓咪身分準確。
- 已實際按位置頁綁定與恢復，並重載看到保存別名／讀回值；位置端是隔離adapter，未操作真攝影機。
- 最終執行版本再走保存與撤回：有效摘要立即更新，原候選與兩份修訂仍保留。
- 360px client/scroll width=345/345；1440px=1425/1425。鍵盤ArrowRight可切頁且只有一個tabpanel可見；console error為空。
- 發現舊pets.js快取使裁切按鈕未出現，已補版本化資源網址；重載後按鈕、對話框、實際保存通過。
- 發現保存後摘要未即時重讀、舊歷史操作按鈕仍可點；已重讀投影並停用舊操作，保留重新載入入口。

## 檢查紀錄與部署界線

- 完整回歸曾733 tests／197.055秒、733 tests／187.132秒，皆OK；最終 **735 tests／182.307秒／OK**，輸出：`reports/v43-three-gaps-accepted-2026-10-16-unittest.txt`。後續僅紀錄文件，不將舊數量冒充本輪結果。
- 七項接線測試passed；Ruff、compileall、相關JS語法檢查passed。
- 沒有建置、發布、升級正式Host、變更go2rtc binary／Xiaomi Cloud/session、防火牆或系統Python／驅動。
- 三項實裝接線已收斂；**正式安裝／真實Provider外送、Telegram收件回條、物理PTZ家庭驗收、MX250長時間負載及5位新手測試仍是NOT_VERIFIED**，不能因此宣稱整份V4.3或正式發布全部驗收。

## 2026-10-16 補充：通知等待時序隔離驗收

在原始接線驗收後，補上可控時鐘情境，沒有修改通知核心：

- AI 在影片可送出後第 3 秒完成，且在 5 秒預算內採用候選摘要。
- Provider HTTP 429 立即使用本機 caption 傳送影片，不等待 Retry-After，也不重試 AI。
- Telegram 影片上傳耗時獨立記錄，不併入 AI grace wait。
- `cat_detected` 會排入語意工作；同一測試中的 `finalized` 不會再排第二份語意工作。
- 原有 0／5／10 秒、超時、晚到結果、多收件者重試、慢轉碼案例仍通過。

本輪驗證命令與結果：

```text
.venv\Scripts\python.exe -m unittest tests.test_delivery_telemetry tests.test_phase2_event_video.Phase2EventVideoTests.test_cat_detected_and_finalized_triggers_do_not_duplicate_semantic_work -v
Ran 10 tests ... OK

.venv\Scripts\python.exe -m unittest discover -s tests -v
Ran 748 tests in 186.891s — OK

.venv\Scripts\ruff.exe check src tools tests — All checks passed
.venv\Scripts\python.exe -m compileall -q src tools tests — exit 0
node --check src/meowkeeper/app/web/static/js/mse-player.js
node --check src/meowkeeper/app/web/static/js/shared-preview.js
node --check src/meowkeeper/app/web/static/js/dashboard.js — all exit 0
```

這些是本機隔離與確定性測試，**不是**真實 Provider 回應、Telegram `message_id`、真實家庭事件影片、家庭 PTZ、硬體長期負載或正式 Windows 安裝版驗收；本輪沒有建置／安裝／重啟正式 Host，也沒有傳送外部請求。
