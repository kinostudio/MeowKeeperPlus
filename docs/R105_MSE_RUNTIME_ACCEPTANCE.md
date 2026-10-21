# r105：MSE 發布依賴與節能比例修正

## 實機故障證據

2026-10-16，Edge 讀到正式 Host 為 r104；首頁、JPEG、AI 持續工作，按流暢立即顯示連線失敗。以無 Session 的實際 WebSocket 握手測得 HTTP 404，沒有到達應回 403 的受保護 WebSocket 路由。

正式安裝版、r104 發布包與建置用內附 Python 都缺少 websockets。pyproject 的 runtime/web extras 已宣告 15.0.1，但 prepared runtime 尚未安裝，build_portable 的必要套件清單也漏檢；先前使用有此依賴的 .venv 隔離測試，未覆蓋終端實際執行環境。

## 修正

- 建置用 embedded Python 補入 PyPI 的 websockets 15.0.1，不修改系統 Python、驅動、CUDA 或正式 App。
- 建置必須確認 websockets 存在、固定版本、client/server 模組皆能由內附 Python 匯入。
- Host 啟動先檢查 WebSocket 元件，明確指定 Uvicorn transport；缺件以繁體中文停止，不再默默降成只有 HTTP。
- 移除 JPEG 與 MSE 媒體本身的 70vh 限制，使用容器完整寬高與 object-fit:contain。保留來源比例，避免大螢幕容器撐開而媒體縮小留白。
- 補入缺少 transport 的建置回歸與已登入完整 FastAPI WebSocket 路由的媒體傳遞測試。

## 隔離實機驗收

使用建置用內附 Python 3.13.15、實際 create_app、正式 HTML/CSS/JS、記憶體測試 Session，以及正式既有 xiaomi_camera_2。只讀取共享 JPEG 並新增 MSE 觀看 consumer，沒有啟動攝影機 ingest 或 FFmpeg，也沒有使用正式登入憑證。

- 即時頁 MSE：1920×1080，播放時間 4.72 → 38.73 秒，readyState=4、paused=false，HUD 20.3 FPS。
- 再次休眠／節能／流暢切換：重新播放至 34.01 秒，HUD 19.8 FPS，未回退。
- 即時頁 JPEG：天然尺寸 1920×1080；圖片與容器均 1366.22×768.5，object-fit=contain、max-height=none。
- 首頁預設節能：圖片與容器均 768.56×432.31；切到流暢成功解碼 1920×1080。
- 首頁 390px：圖片與容器均 305.33×171.75，頁面寬 375px，沒有水平溢出；驗收後恢復瀏覽器原視窗。
- 休眠：JPEG/video 都 hidden 且清空 src；go2rtc 回到 producer=1、consumer=1、WS consumer=0，核心監控仍提供新 JPEG。
- 全螢幕請求被本次瀏覽器環境拒絕，未列為通過；本輪未改動該權限或按鈕。

截圖保存在本機 reports/r105-mse-live.jpg、reports/r105-energy-live.jpg，含攝影機畫面，不列入公開發布包。

## 測試與發布

31 項預覽／發布專項通過；完整回歸 788 項／240.080 秒／OK，紀錄 reports/r105-unittest.txt；Ruff、compileall 通過。第一方 src/tools/scripts/configs/docs 的 431 個文字檔，Telegram/GitHub Token 及完整數字 Xiaomi URL 樣式掃描命中檔案 0；不宣稱涵蓋任意秘密或二進位內容。

候選包：dist/MeowKeeperPlus-portable-2026-10-16-r105，20,714 個 manifest 檔案。包內 Python 執行 verify_portable：passed、private_data_present=false。Host、建置工具、預覽 CSS、寵物設定 template 與 repository SHA-256 一致；包含既有 DINOv2 實驗模型及已驗證 PTZ backend。

改用 r105 **包內** Python 和 **包內**實際 app/templates/CSS/JS 再驗收：頁尾確認 r105 可攜版，MSE 1920×1080 播放時間 14.589972 → 48.669158 秒、HUD 19.8 → 20.0 FPS、readyState=4、paused=false。再切節能 → 休眠 → 流暢，重新播放至 23.706713 秒，沒有回退；包內 JPEG 圖片與容器同為 1366.22×768.5。資料保存在 reports/r105-packaged-playback.json；截圖 reports/r105-packaged-mse.jpg。

包內 MSE 播放時同一來源 producer=1／consumer=2／ffmpeg.exe=1；休眠時 producer=1／consumer=1／ffmpeg.exe=1。正式 /healthz 保持 200。測試觀看端與兩次隔離 server 均已關閉。未讀取、改寫或沿用正式 Session，測試 cookie 使用 localhost，正式登入使用 127.0.0.1，避免覆蓋正式登入狀態。

正式 8000 埠仍為 r104；依使用者升級規則，由使用者自行安裝候選包後才能宣稱正式部署通過。隔離實機成功不等於正式 Host 已更新。

本段為候選包建置後的 repository 補記，未回寫已產生 manifest 的發布包。
