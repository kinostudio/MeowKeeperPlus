# Host CPU 等待策略（2026-09-27）

## 實測原因

此處原實機紀錄封存於維護者本機；公開文件不包含個別設備結果。

## 最小修正

正式 `run_host.main()` 在模型 lazy import 前對本程序使用 `setdefault`：

- `OMP_WAIT_POLICY=PASSIVE`
- `KMP_BLOCKTIME=0`

不改模型、信心門檻、輸入大小、採樣 FPS 或執行緒數，不修改系統環境。
已有明確環境覆寫時保留原值。此設定不會改變已啟動的服務，也不會因 import run_host 自動套用。

## 後續驗收

發布切換後重新測量真實 Host CPU、延遲、JPEG 更新、cat 正例及 Telegram 閉環；
不可直接把隔離測試結果宣稱為正式服務的結果；實機量測紀錄只保留在維護者本機。
