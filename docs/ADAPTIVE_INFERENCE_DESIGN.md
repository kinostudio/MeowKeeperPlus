# 自適應 CPU／GPU 推論設計

日期：2026-10-11。狀態：AI_PROCESS_AND_HIGH_RESOLUTION_IMPLEMENTED／REAL_HARDWARE_ACCEPTANCE_PENDING。

本文件同時記錄設計契約與目前實作狀態。repository 已完成獨立 reader、容量 1 信箱、可回收 AI 推論程序、640／960／1280／1920 實際模型輸入、負載回退及 Dashboard 設定接線。CPU 真實模型測試通過；MX250／GPU 長時間效能與正式 Host 部署仍未驗收。詳見 [實作報告](ADAPTIVE_INFERENCE_IMPLEMENTATION_REPORT.md)。

## 1. 目標與限制

- 使用者偏好：長期硬體負載以 40% 為目標；不是瞬間硬上限，也不是跨所有電腦的效能保證。
- CPU 與 GPU 都是正式推論路線；沒有 GPU、CUDA 不可用或 GPU 故障時仍可使用 CPU。
- 原始攝影機畫質、壓縮環形錄影與預覽尺寸不因 AI 降級而被修改。
- 使用同一來源、同一 ingest；不新增模型、不切換攝影機 profile、不動系統 Python／驅動／CUDA。
- 使用者設定與實際執行值分離。自動降級不得覆蓋保存偏好；重啟重新評估，不盲目恢復之前的高負載值。
- MX250 最低 profile 仍以 batch 1、640、最高約 5 FPS 為保守上限；較高模型尺寸須明確選取進階 profile 並實測。

## 2. 原始碼 audit（本輪讀取）

| 邊界 | 已確認狀態 | 必要工作 |
|---|---|---|
| `core/detection/frame_analysis.py` | PyTorch CUDA／CPU；支援 640／960／1280／1920，回報實際 `inference_size` 與來源尺寸 | 已實作；尺寸不超過來源長邊，不補造細節 |
| `core/inference/runtime_device.py` | GPU RuntimeError 時同一影格 CPU 重試一次，後續維持 CPU | 保留既有一次性故障語意；不是持續負載控制 |
| `app/core_controller.py` | 單一 ingest + latest-frame-wins reader；基本偵測與 V0.2 BoT-SORT 推論呼叫獨立 process worker | 已實作；V0.2 的身分／行為／資料庫協調仍在 Host，非所有 AI 工作都在子程序 |
| `core/media/ingest.py` | FFmpeg stdout JPEG iterator 每讀一張 yield 一張 | 持續消耗 stdout，避免 AI 阻塞造成 pipe 回壓 |
| `app/web/state.py` | 已有整機 CPU／RAM、可驗證應用程序 CPU／RSS 彙整 | 無觀看者也須採樣；Dashboard 查詢不能成為控制時鐘 |
| `core/events/v02_host_analyzer.py` | 可注入獨立 BoT-SORT tracker proxy；身分／行為／資料庫協調保留在 Host | 已接線；隔離追蹤推論與基本 detector 都有測試 |

媒體 reader 的慢推論隔離與獨立 AI worker 已有本機測試。HTTP camera／MX250 的長時間穩定性仍需家庭硬體驗收；若 AI worker 達重啟上限，狀態會暫停 AI 而保留 Host 媒體／錄影路徑。

## 3. 管線邊界（先完成，再調負載）

```text
單一 FFmpeg ingest ── 壓縮分段 → 環形快取／事件組片
       │
       └─ 持續 JPEG reader → 最新預覽快取
                              └─ 容量 1 最新影格信箱
                                   → 獨立 AI worker
                                   → 有界結果佇列 → 事件／通知
```

- reader 不執行 YOLO、身分模型、SQLite 交易或 Telegram；信箱滿時覆蓋尚未分析的旧影格並計數。
- AI worker 只能消耗共享影格，不自行拉 RTSP。任何時刻只允許一個推論 worker、一個高頻視覺模型、batch 1。
- 結果佇列有界；事件處理／SQLite 重試不能堵塞 JPEG reader。佇列滿時留下安全計數與告警，不無限堆積。
- 推論 worker 採獨立程序，才能在 native 推論無回應時有限終止／重建；Python 執行緒逾時不能當作已取消模型工作。
- 替換 worker 前先確認舊 PID 結束與 CUDA 資源釋放；失敗則 AI 標示 suspended，不能並行啟動第二模型。
- 追蹤 worker 重建必須更換 session namespace；永久 pet_id 不受短期 track 重編號影響。未完成身分先保存 unknown。
- 讀取時間與來源捕捉時間分開；僅有接收時間時明確標為 estimated，不捏造攝影機時間。過期結果不得冒充即時觀測。

## 4. 負載定義

40% 不套在 RAM／VRAM 容量，也不把 CPU、GPU 相加。

| 指標 | 來源及用途 |
|---|---|
| app_cpu_percent | 已驗證 MeowKeeper+ Host／worker／FFmpeg／go2rtc 等程序 CPU，除以邏輯核心數，0～100% 整機尺度 |
| system_cpu_percent | 整機含其他軟體；只用於資源壓力提示，不歸因給 MeowKeeper+ |
| hottest_core_percent | CPU 模式偵測單核心瓶頸，不因整機低於 40% 就忽略推論積壓 |
| device_gpu_percent | 驅動可用時的整張 GPU 使用率；不是本程序專屬 GPU 使用率，UI 必須標清楚 |
| inference_busy_ratio | 序列 worker 推論 wall time／觀測窗；輔助 duty cycle，不冒充真實 GPU 使用率 |
| latency_p50／p95、achieved_fps、frame_age、dropped_frames | 工作速度與過期程度；GPU 時間需要完成同步後量測，不只計算提交時間 |
| available_ram／process_rss／free_vram | 獨立容量保護。MX250 2 GiB 預算仍有效，系統 RAM 不要求低於 40% |

背景採樣每 2 秒，60 秒滑動窗；記憶體只保留有界樣本。首個 CPU 樣本、程序重啟與 PID 重用需重新建立基線（PID＋建立時間），不把缺值當 0。

GPU 遙測不可用時標示 unavailable，使用 latency／frame age／busy ratio 判斷；不能宣稱已達成「GPU 低於 40%」。不得只為遙測安裝驅動。其他軟體造成整機壓力時可降低自身工作，但提示歸因不明。

### 溫度保護（可選的第二層）

- 建議加入 CPU／GPU 溫度保護，但不作安裝或啟動的必要條件。只使用已可取得、來源與單位可驗證的遙測，不自動安裝監控驅動、不要求管理員權限才能監控。
- 感測器讀不到、值過期、非有限數值或來源不明時標示 unavailable；不当作 0°C，不因此停掉正常 AI。
- 每個裝置分開設定 `warning_celsius`、`critical_celsius`；未確認該硬體的安全門檻時預設 null，顯示尚未設定，不把固定 80／85／90°C 套在所有機型。
- 可依已確認的硬體規格建議門檻，由使用者確認；GPU 與 CPU 不共用同一個門檻。這是補充保護，不取代硬體／作業系統本身的熱管理。
- 超過 warning 持續 30 秒：先暫停低優先身分工作，再逐級降低 AI 尺寸／FPS；若媒體處理本身造成高溫，提示降低預覽負載，但不自動改錄影畫質。
- 超過已設定 critical 持續兩個有效樣本：暫停 AI，保留影音與狀態並發出有冷卻的警告。即使負載低於40%仍生效，因環境、散熱與其他程序也可能造成高溫。
- 恢復須低於 warning 減 5°C 且穩定5分鐘，同時滿足容量／負載條件；critical 後預設由使用者按「重新嘗試 AI」，不因溫度回落立即滿速重啟。
- CPU fallback 不能解決所有 GPU 高溫，也可能增加 CPU 熱負載；有高溫故障時先進入低負載／暫停，再確認目標後端溫度。不能無條件轉移熱負擔。
- 狀態顯示感測器來源、採樣時間、目前溫度與門檻是否已驗證；測試包含短暫尖峰、持續高溫、critical、缺值與恢復防抖。

## 5. 設定契約

目前 Dashboard 使用 `dashboard_settings.analysis_width` 保存分析長邊，合法值 640／960／1280／1920，預設 640。MX250 最低 profile 仍然採用 640；選擇較大值是使用者明確提高負載，不代表 MX250 已證明可穩定承受。設定保存後需重啟 Host 套用，runtime 的有效尺寸可因回退低於保存偏好。

下列較完整 `inference` JSON 描述仍是後續可擴充契約，不表示這些所有欄位都已接入設定頁：

```json
{
  "inference": {
    "mode": "adaptive",
    "backend_preference": "auto",
    "performance_profile": "mx250_minimum",
    "preferred_input_size": 640,
    "preferred_fps": 5,
    "minimum_input_size": 640,
    "minimum_fps": 1,
    "target_utilization_percent": 40,
    "sample_seconds": 2,
    "window_seconds": 60,
    "sustained_overload_seconds": 60,
    "recovery_seconds": 300,
    "recovery_utilization_percent": 30,
    "auto_upgrade": false,
    "worker_timeout_seconds": 15,
    "worker_restart_limit": 2,
    "worker_restart_window_seconds": 600
  }
}
```

- backend_preference：auto／cpu／cuda；cuda 不可用時顯示原因並允許 CPU，不偽造 GPU Ready。
- 進階 profile 的候選長邊：640／960／1280／1920；1920 長邊不保證模型 tensor 就是原始 1920×1080，需回報縮放、stride 對齊、padding 後 H×W。
- 可用階梯：不高於使用者偏好的已驗證尺寸；FPS 5／3／2／1，再取不高於使用者設定的值。
- 此次 40%、60 秒、5 分鐘、15 秒均為待實測的初始設計值；設定範圍需 schema 驗證。目標占用可先限制 10～90%。
- RAM 預設安全餘裕可先採 max(1 GiB, 實體 RAM 的 10%)；VRAM 由 profile 預算與可觀測可用容量限制，不把固定空間條件当成所有電腦通用。
- 偏好參數寫 config；effective_backend／effective_size／effective_fps／reason／last_transition 只作 runtime 狀態，降級歷史以限量輪替日誌保存。

## 6. 狀態與回退

1. **starting／warming_up**：驗證模型與後端，從 640、低频（1 FPS）開始。暖機期間不以首次模型載入耗時判斷穩態，也不阻塞 ingest。
2. **running**：維持目前有效設定；不直接跳到使用者高解析度。較高尺寸需使用者同意一次有界試跑。
3. **degraded**：可靠指標持續超標，或 latency／影格年齡顯示採樣無法跟上時，一次只降一級；每次轉換後重新收集有效視窗。
4. 尺寸由大至小降至 640；仍超標則 FPS 5→3→2→1。暫停低優先身分／背景统计，再觀察；主錄影不降攝影機 profile。
5. **cpu_fallback**：GPU OOM／CUDA 故障不等待 60 秒；確認舊 GPU worker 結束後從 CPU 640／1 FPS 開始，不帶著 GPU 高解析度設定直接重試 CPU。連線／媒體錯誤不觸發 backend 切換。
6. **suspended**：CPU 在最低檔仍不能跟上、容量危急或推論反覆錯誤時，只暫停 AI。狀態清楚顯示「録影持續，貓咪偵測暫停」，不得回報「目前沒有貓」。
7. **recovery_available**：負載低於恢復門檻且容量足夠持續 5 分鐘，提示手動試跑；預設不自動升級、不自動返回故障 GPU。

單一尖峰不觸發降級；OOM、worker 無回應與容量危急是立即保護條件。無回應 worker 最多在 10 分鐘內重建 2 次，超過則 suspended，不無限 retry。資源恢復後只做一次有界健康試跑或由使用者按「重新嘗試 AI」；不能一直停留在暫停卻無恢复操作。

40% 是軟預算。不能對一次 native 推論提供百分比硬限制；如 ingest／解碼本身已超標，AI 降級到最低也可能無法達標，需提示「媒體處理負載較高」，另提出降低預覽尺寸／FPS 的建議，由使用者決定，錄影維持不變。

## 7. Dashboard 與通知

- 模式：自動調節（建議）／固定上限；兩者都保留故障與容量保護。
- 顯示「偏好：1280／5 FPS；實際：960／3 FPS；後端：CPU；原因：推論耗時持續超標」。
- 顯示 CPU 用量口徑、GPU 是否可觀測、分析 FPS 與預覽 FPS 分開；不能把1080p來源規格當模型實際尺寸。
- 操作：「嘗試較清楚的 AI 分析」「恢復保守設定」「重新嘗試 AI」；失敗自動回到上一個安全檔，不需重啟整個 Host。
- 說明：較清楚可能改善遠處小貓，但不保證更準；調低 AI 不改錄影。低 FPS 可能漏掉短暫動作，行為須保持 experimental。
- 故障／暫停可透過既有 Telegram 系統狀態佇列通知；只向啟用且有權限的對象，轉換去重冷卻，不逐樣本轟炸。
- AI 暫停期間的統計是未監測，不是沒有貓；缺失區間不得補算睡眠／休息。

## 8. 實作順序與驗收 Gate

### A：讀取與 AI 隔離

容量 1 信箱、獨立 worker、結果有界、有序關閉與有限重建。注入慢 AI、15 秒卡住、CPU 錯誤、資料庫短暫鎖定；FFmpeg 必須繼續產生可讀壓縮片段，預覽 reader 持續更新。來源／FFmpeg producer 數量不增加，無孤兒 worker。

### B：遙測與純策略

以可注入單調時鐘及假樣本測試：短尖峰不降、持續超標一次一級、缺值不當零、恢復延遲、CPU 單核心瓶頸、GPU 外部負載／未知歸因、低記憶體與重試上限。只靠策略單元測試不算硬體驗收。

溫度為可選控制：沒有可靠感測器或尚未設定硬體門檻時仍可完成負載策略；報告溫度驗收為 not_checked，不用假溫度冒充實機。

### C：模型／追蹤與 UI 接線

解除進階 profile 的程式 640 硬上限，基本 detector 與 tracker 同步尺寸；保持 cat-only、confidence 門檻、batch 1。測試回退後 model parameter device 與 UI backend 一致，追蹤 namespace 不重用；保存偏好不被有效值覆蓋。

### D：真實硬體與發佈

- 使用合法本地含貓影片，固定素材與時間窗比較 CPU、可用 GPU 的640／960／1280／1920長邊，先暖機，再各穩態觀察至少 5 分鐘；不與正式攝影機搶連線。
- 記錄實際 tensor H×W、p50/p95 latency、achieved FPS、CPU、GPU 可觀測性、RSS、VRAM、掉幀、片段間斷與事件 clip 完整性。較高尺寸要附小貓有標註素材的偵測比較，不只看速度。
- 有效門檻：可靠的60秒窗指標穩態符合預算，或安全降級並清楚顯示未能達標；不能用平均數掩蓋持續超標。硬體無法在最低檔達標時驗收 suspended 流程，而不是宣稱效能通過。
- 至少 2 小時監控循環測試，包含 GPU 故障注入／純 CPU／無觀看者／兩個預覽 client；録影持續、worker 數有界、偏好不变、Telegram 不阻塞。
- MX250／8 GiB、無 GPU Windows 與正式安裝 Host 分開驗收；RTX 2060 結果不代替 MX250。
- 建包完整性檢查後，給使用者精確升級代碼；不自動 UAC 或安裝。

## 9. 本輪完成界線

### 已實作並由本機驗證

- 基本 YOLO 與 V0.2 BoT-SORT 推論在可監督的 spawned child process 執行；IPC 一次一個請求，不建立無界推論佇列。
- Worker 啟動／推論有逾時；逾時只終止 AI 子程序，有限重建，達重啟上限後停 AI，不停止 Host 與影音 reader。明確停止監控時也會回收子程序。
- Dashboard 保存 `analysis_width`；模型支援 640／960／1280／1920，FFmpeg 單一 JPEG 分支會保留所需長邊（最高 1920，且不放大來源），結果包含實際模型輸入尺寸。
- 過載逐級降低尺寸／FPS；CUDA 故障立即降為 CPU 640／1 FPS。V0.2 尺寸變更會傳到 tracker child。
- 本機公開貓圖真實 YOLO child-process 測試涵蓋四個輸入尺寸；另有 process timeout／kill／重啟測試。

### 尚未驗收

- 本機測試證明尺寸與程序隔離，不證明較高解析度一定提高身分或行為準確度。
- MX250、GPU 熱狀態、實際家用來源的 CPU／GPU 使用率、至少 2 小時循環及正式安裝 Host 尚未測試／更新；不宣稱硬體效能 Gate 或 24H Gate 完成。
- V0.2 的 DINOv2 身分 embedding、行為協調及 SQLite 存取並未全部搬到 child process；隔離範圍是主要 YOLO／BoT-SORT native 推論。
