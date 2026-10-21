# 已有 Xiaomi 畫面但設定頁顯示未接入：r83 修正

## 真實證據

- 正式 Host PID 11172，Dashboard bind 0.0.0.0:8000；go2rtc PID 29356 的 API/RTSP 分別只綁 127.0.0.1:1984/8554。
- 已安裝 `setup_service.py` 與 `run_host.py` 雜湊和修正前 repository 相同；不是單純裝錯 r82。
- 保存來源為本機 RTSP `xiaomi_camera_2`；真實 producer 的 format 為 xiaomi/miss，user-agent 機型為 chuangmi.camera.026c02。
- 在獨立暫存狀態中，以新版服務讀取上述正式 producer，裝置配對恢復成功、畫質控制可用。
- 沿用現有串流完成 FFprobe 與 JPEG 解碼驗證：1920×1080、20 FPS、HEVC。
- 真實 PTZ 狀態查詢為 available；本輪沒有馬達移動或即時位置讀回驗收。
- 同一串流 producer/consumer 前後均為 1/1；沒有替換來源、開啟第二條攝影機連線或重啟正式 Host。

## 根因與修正

1. 啟動時只配對一次；producer 尚未暖機或精靈原本為 failed 時，之後不再恢復控制。現在裝置查詢會限速重試唯讀配對，最多每 5 秒一次，已正確配對時不重複查詢。
2. 正在監控的攝影機仍會建立暫存 SD probe。這條獨立 camera source 可逾時，並把精靈降級成未接入。現在已接入裝置只讀現有 stream 的資訊，不替換畫質；資訊讀取失敗保留 connected 並明確標示「畫質資訊讀取失敗」。
3. 前端的舊 probe 回應與 cloud online_status 遮蓋目前播放狀態。現在以活動來源配對及 Host 的 15 秒內最新影格顯示監控中，缺少新影格則顯示等待，不以註冊 producer 冒充影格。
4. 按已接入且持續收到影格的攝影機「接入」會沿用來源，不重啟 ingest。

## 回歸與發布

- 42 項相關測試通過，涵蓋 producer 延遲、限速重試、活動串流 probe 失敗不中斷監控、接入冪等、無影格不顯示監控成功、遠端控制與 PTZ。
- 既有 .venv 的完整 490 項 unittest 通過；完整 Ruff、獨立快取 compileall、設定頁 inline JavaScript 語法檢查通過。
- embedded runtime 直接執行開發測試的初次嘗試因隔離搜尋路徑及 Python 3.13 暫存資料庫清理問題失敗；不得把初次結果寫成通過。改用未更動的既有 Python 3.10 .venv 後完整通過。
- r83 發布包驗證 passed，19100 個 manifest 檔案，private_data_present=false；套件未啟動，因此未在發布包內產生 runtime 狀態檔。
- r83 保留已選用的 ptz-feedback 後端。使用者自行升級；建置成功不等於已安裝。
- 升級後仍須驗收正式 Dashboard 的畫質套用及真實馬達操作；本輪真實驗證為既有影像解碼、服務配對與 PTZ 狀態讀取。

## 避免再次誤判

- 已有主路徑影格時，先核對目前來源與活動 producer，不再另開同台攝影機 SD source。
- cloud region、cloud online_status、FFprobe 測試結果與主監控活動狀態是不同資料；一次輔助 probe 失敗不能否定現有影格。
- 本機 RTSP 是否開放區網，以實際 listener 為準。本輪 8554 只綁 loopback；Dashboard 0.0.0.0 不代表 RTSP 也開放。
