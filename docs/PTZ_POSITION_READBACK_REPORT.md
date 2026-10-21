# Dashboard 位置讀回接線與實機驗收

日期：2026-10-10（Asia/Taipei）。

## 本輪變更

- 即時畫面按鈕改為「讀取目前位置」，就地說明不移動鏡頭、裝置座標不是角度。
- 前端明確 GET `/api/v1/cameras/xiaomi/devices/{opaque_id}/ptz?refresh=true`；
  既有登入驗證維持不變。預設 GET 仍只讀快取，沒有自動重試或位置輪詢。
- 本機 go2rtc 的真正 refresh 查詢使用 8 秒客戶端逾時，讓服務端 5 秒的馬達等待能回報結果。
- 必須同時符合 fresh refresh、connected=true、ret=0、angle/elevation 為 1～101 的整數，
  才設定 position_readback_verified=true；缺座標、錯誤 ret、單純 connected 均不通過。
- 座標只顯示在本次有效回應後；失敗明確標示無有效位置。位置隱私按鈕預設停用，
  只有本次位置驗證且 absolute_move 能力為真才啟用；更換裝置清除驗證。
- 遠端中繼目前沒有即時 refresh 契約，明確 unsupported，不用遠端 connected 假裝已讀位置。
- 隔離工具新增 --position-only：只發一次位置查詢，不發 move/stop，不替換正式 binary。

## 真實結果

原始證據：`reports/ptz-026c02/position-20261010.json`。

- model：chuangmi.camera.026c02。
- isolated API：21984；producer=1、consumer=1。
- HTTP 504，response_timeout；position_readback_verified=false。
- 結果：**位置讀回 failed**。不能宣稱裝置支援角度定位或精確隱私位置恢復。
- 本輪未轉動鏡頭。official_binary_replaced=false。
- official_monitor_restored=true；額外 healthz 核對 status=ok、core_status=running。
- 先前上下實體移動成功仍成立，但它不是位置讀回證據。

## 檢查與部署邊界

- 56 項相關 unittest 通過；受影響檔案 Ruff 通過；相關 Python compileall 通過。
- repository 程式接線已修改；**未建置新發布包、未升級正式 Host**。
- 正式瀏覽器的按鈕與真實位置成功端到端驗收尚未通過，不能用程式測試替代。
- 不將原始真實回應或 Session 寫入文件。後續若發布，只提供安裝代碼，由使用者自行執行。

## 後續處理

先查證此型號的馬達位置命令／回應協定，不能重複相同查詢來代替新證據。
在取得有效位置前，只允許已驗證的相對方向能力；不啟用精確位置操作。

## 同日後續修正：位置讀回已通過

此節取代上方舊實驗版「位置讀回 failed」的目前狀態；保留舊證據以供比對。

- 上游修正來源：bkulpa/go2rtc commit `c88e1c980d7ac565bd23cee2b992a7ca34f19990`。
- 以 korasino/go2rtc `e746789ecbcfe663d6e22a69eac586813b4cb575` 為基線，建立
  `reports/ptz-feedback-fix/source`，沒有修改原來源副本或原實驗 binary。
- CS2 ReadCommand 改為 BigEndian；馬達 JSON 解析前 TrimRight NUL。
- 新增對應 Go 測試：兩個相關 Go package 測試通過；13 項 Python 測試及相關 Ruff 通過。
- 讀回有效座標允許 0～101；這不表示絕對定位寫入範圍也已驗證。
- 新 binary：`reports/ptz-feedback-fix/go2rtc-ptz-feedback.exe`，SHA-256
  `3F64D4C5144350DC816ABB4DE41BD686E248A9528EB2825A00CD8FC65EFEA0F3`。
- 單次 `--position-only --feedback-fix` 實機結果：水平 75、垂直 29、ret=0，
  connected=true、position_readback_verified=true、producer=1、consumer=1。
- 證據：`reports/ptz-026c02/position-feedback-fix-20261010.json`。
- 本輪未送移動命令；正式監控恢復。正式 binary 前後 SHA-256 均為
  `923D57252E8139A69C52E4ACC1E399A640244A8EF457FD9B7267A25847D68F8C`。
- 根因判斷：舊版 CS2 回應 ID 解析錯誤會令加密馬達回應被忽略；NUL 防護同時加入。
  本輪兩項合併修正成功，未做單項 A/B，不能宣稱這台裝置兩項都單獨造成故障。
- 尚未驗證：絕對位置寫入、隱私位置保存／恢復、其他機型、正式 Dashboard E2E。
- 正式 Host 未升級；前端相關 repository 接線仍待打包後由使用者自行安裝。
