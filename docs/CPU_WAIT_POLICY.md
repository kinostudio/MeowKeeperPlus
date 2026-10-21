# Host CPU 等待策略（2026-09-27）

## 實測原因

r18 真實攝影機兩小時觀測的 Host CPU 約 685%（psutil 多核心程序尺度，不是整機百分比）。
以 r19 內附 runtime、同一張現有 JPEG、CPU YOLO11n、20 次推論與 5 FPS 節流做隔離比較：

| 設定 | 程序 CPU | 平均推論延遲 |
|---|---:|---:|
| 預設 8 threads | 699.80% | 50.08 ms |
| 2 threads | 140.75% | 84.70 ms |
| 8 threads + passive wait | 96.26% | 59.87 ms |
| 2 threads + passive wait | 58.45% | 73.62 ms |

這是短時間、非隨機順序的診斷，支持等待策略為高 CPU 的重要因素；不是 MX250 基準，
也不是新版真實 Host 長時間負載證明。素材沒有貓，結果全部 0，不作正例召回率驗收。

## 最小修正

正式 `run_host.main()` 在模型 lazy import 前對本程序使用 `setdefault`：

- `OMP_WAIT_POLICY=PASSIVE`
- `KMP_BLOCKTIME=0`

不改模型、信心門檻、輸入大小、採樣 FPS 或執行緒數，不修改系統環境。
已有明確環境覆寫時保留原值。此設定不會改變已啟動的 r18，也不會因 import run_host 自動套用。

## 後續驗收

發布切換後重新測量真實 Host CPU、延遲、JPEG 更新、cat 正例及 Telegram 閉環；
不可直接把隔離測試的 96% 宣稱為正式服務的結果。
工作區證據：`work/cpu-inference-threads-0.json`、`work/cpu-inference-threads-2.json`、
`work/cpu-inference-threads-0-passive.json`、`work/cpu-inference-threads-2-passive.json`。
