# r88 系統硬體與一小時資源統計

發行日期：2026-10-12。

## 本輪範圍

- Telegram 狀態接入同一 Host 的 resource-history.db，版本 1 → 2 可重跑 migration，不丟棄既有 CPU／RAM／磁碟占用資料。
- 基本硬體、CPU／RAM／可用 GPU 利用率、CPU／GPU 可用溫度的最近／平均／採樣峰值，以及整機磁碟 I/O。
- 不足一小時及感測器不可用均明確說明。完整語意見 [資源歷史](RESOURCE_HISTORY.md)。
- 同時封裝前輪的原始解析度截圖、動態 ONNX GPU 後端；預覽仍可縮放。原生快取截圖最多有分段／關鍵幀時間差。

## GPU 驗收界線

開發 Python 3.10 使用 OpenVINO 2024.6；內附 Python 3.13 使用相容的 2025.4.1。
開發機 RTX 2060 短測約 5 FPS、GPU 平均 29%；Intel UHD 2024.6 短測約 4.68 FPS。
這些不是 MX250、正式包 Intel 2025.4.1 或 24H 負載保證；GPU 初始化或執行失敗須 CPU fallback。
匯出模型使用相同官方 YOLO11n，batch 1、動態矩形輸入；1920×1080 以 stride 32 補邊為 1920×1088。
匯出 BoT-SORT 保留追蹤但不含 backbone ReID，不額外下載模型；永久身分仍為 experimental。

## 部署

使用者自行執行新包 Install-MeowKeeperPlus.cmd -Upgrade；本輪不啟動安裝器、不重啟正式 Host。
新欄位從升級後開始累積，不能恢復過去未收集的溫度／GPU／磁碟 I/O。
529 項 unittest 通過；Ruff、compileall、開發／內附 runtime pip check 通過。
TOML／JSON／UTF-8 檢查通過；本輪 telemetry／status 修改檔的 Bot Token／含密碼 RTSP 掃描為零命中。
正式包 manifest 與內附 runtime smoke 另行執行；不得將本機生成文字視為真實 Telegram message_id 驗收。
