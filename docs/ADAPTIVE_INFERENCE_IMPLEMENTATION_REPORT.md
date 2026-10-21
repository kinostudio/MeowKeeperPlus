# 獨立 AI 程序與高解析度推論驗收報告

日期：2026-10-11。狀態：`AI_PROCESS_AND_HIGH_RESOLUTION_CODE_ACCEPTED`；`REAL_HARDWARE_ACCEPTANCE_PENDING`。

## 驗收範圍

本次驗收的是 AI 推論與媒體 ingest 的程序隔離，以及 640／960／1280／1920
推論尺寸的設定、傳遞與真實 YOLO 推論路徑。不代表 MX250 效能、GPU 溫度、家庭攝影機
或正式安裝 Host 已完成實機驗收。

## 已實作

- YOLO 偵測與 BoT-SORT 追蹤推論以 Windows `multiprocessing` spawn 子程序執行；每個
  worker 僅處理一個同步 IPC 請求，不會因 AI 變慢而累積無界影格佇列。
- worker 啟動與推論均有逾時。推論逾時會終止並回收該 AI 子程序；之後的請求才計入
  有限重建額度（預設 10 分鐘內最多 2 次）。重建耗盡時只暫停 AI，不停止 Host、FFmpeg
  影音 ingest、預覽或壓縮環形快取。正常停止 Host 也會回收 AI 子程序。
- 即時 JPEG reader 持續消耗同一條 FFmpeg ingest；AI 使用容量為 1 的 latest-frame-wins
  信箱。AI 卡住或回收時，影音 reader 和環形快取仍繼續前進，不建立第二條攝影機連線。
- Dashboard「AI 分析解析度（長邊）」可保存 640／960／1280／1920；預設仍是 640。
  保存偏好與即時有效值分離，設定需重新啟動 Host 套用；負載回退可以降低有效尺寸而不
  覆蓋使用者偏好。高於來源長邊時不會放大來源影格來假造細節。
- 同一 `analysis_width` 傳入基本 YOLO 與 V0.2 tracker worker；單一 ingest 的影格寬度
  依預覽、AI 與 V0.2 身分用途中最高需求選擇，最高不超過 1920；來源畫質、錄影片段及
  攝影機 profile 不因 AI 尺寸而被修改。
- 狀態包含 AI worker PID／存活狀態／重建數、推論後端、偏好與有效輸入尺寸、有效 FPS、
  推論延遲及負載遙測可用狀態。CPU 後端量測範圍是 AI 子程序 CPU 正規化值；GPU 遙測若
  可用則為整張 GPU，包含其他程式，不偽稱為 MeowKeeper+ 專屬 GPU 使用率。
- 過載／CUDA 故障的安全回退保持 640／1 FPS；AI 狀態回退不會改錄影解析度或中斷媒體。

## 驗收證據

- 全部 unittest：486 項通過（約 130.6 秒）。包含真實本地 YOLO11n 子程序在
  640／960／1280／1920 四個設定值上均偵測到含貓圖片、回報對應 `inference_size`，
  並確認 worker PID 與測試 Host 不同；同套件亦驗證非貓圖片不產生貓事件。
- 這個四尺寸測試使用本地含貓圖片，先將測試影格縮放到 1920 長邊再逐尺寸推論；它證明
  高尺寸參數確實到達真實模型並能完成推論，不證明高解析度能提高身分／行為準確度，
  也不是原生 1080p 攝影機畫質驗收。
- 真實 bundled FFmpeg 產生並解碼 8 秒、640×360、5 FPS 本地影片。AI 子程序被刻意設為
  0.2 秒推論逾時後，reader 仍排空來源、收取至少 35 張 JPEG、環形快取仍保留至少 7 秒；
  AI worker 已停止、FFmpeg restart count 為 0，證明此受控案例沒有因 AI 逾時重啟媒體來源。
- 對 worker 的逾時回收、下一個請求有限重建、重建上限與上述 FFmpeg 整合案例，針對測試
  6 項通過。重建數只在實際嘗試建立替代 worker 時增加。
- `ruff check src tools tests`：通過；`compileall -q src tools tests`：通過；
  `git diff --check`：通過。

## 環境與未完成項目

- 真實 YOLO 執行後端：CPU（本次執行環境的 PyTorch 為 CPU build）。未觀測到本次真實
  CUDA 推論，不能宣稱已使用 GPU。
- repository `.venv\Scripts\python.exe` 在目前 Windows 環境無法啟動，回傳
  `0xC0000135`（必要 DLL 載入失敗）。為避免修改系統 Python／驅動／CUDA，本次使用同一
  基礎 Python 3.10，並以 process-local `PYTHONPATH` 載入 repository source 與既有
  `.venv` 套件完成驗收；沒有安裝或變更全域環境。
- 尚未用 MX250、2 GiB VRAM／8 GiB RAM 基線實測 640／960／1280／1920 的延遲、FPS、CPU、
  GPU、溫度、VRAM 或準確率差異。MX250 建議預設仍維持 640、batch 1、單一視覺模型、約
  5 FPS；其他解析度應逐級實測，不代表可長期穩定滿載。
- 尚未執行 2 小時循環、真實 RTSP／YouTube 長時間來源、正式 SYSTEM Host 更新、或真實
  GPU 卡死／溫度保護驗收。測試只證明受控本地案例的程序回收與媒體隔離。

## 結論

「獨立 AI 推論程序」與「可選高解析度 YOLO 推論」的程式接線及本地自動驗收已完成；
可以建立供使用者自行升級的候選 portable 包。整體硬體 Gate 仍為未完成，正式部署及
MX250 效能不得標記為通過。
