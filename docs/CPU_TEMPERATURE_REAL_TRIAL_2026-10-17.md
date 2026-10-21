# CPU溫度：第一次真實硬體隔離驗收

日期：2026-10-17（Asia/Taipei）。使用者已明確同意安裝官方PawnIO2.2.0受限版核心驅動及一次管理員隔離測溫，並回覆「已安裝」。未升級正式Host、未改GPU來源、未放寬驅動ACL、未建立額外服務／排程工作。

## 已驗證結果

| 項目 | 實際證據 |
| --- | --- |
| 環境 | AERO15本機，Intel Core i7-10875H；不是C701所在地的MX250筆電實測 |
| PawnIO | 安裝登錄2.2.0.0，服務Running |
| 安裝器 | 固定官方2.2.0；hash／簽章於執行前重驗，UAC由使用者核准 |
| 已安裝driver | 版本2.2.0，Authenticode Valid，簽署者Microsoft Windows Hardware Compatibility Publisher |
| driver SHA256 | FCA6E7D58B0CF38DBB913A2B9E532F48629145D395F454B16A9F58E97B8D3940 |
| CPU來源 | LibreHardwareMonitorLib0.9.6，真實CPU Package及8個Core；不是ACPI、主機板或由使用率推估 |
| Package六筆 | **91、89、86、90、90、87°C**；最低86、最高91、算術平均88.83°C |
| 採樣時刻 | **2026-10-17 15:31:26～15:31:36 GMT+8**；這是當時讀值，不是持續目前值 |
| Helper | 10.373秒有限試驗，執行完畢退出，驗收後未發現該程序 |
| 記憶體 | 此次process peak working set **26.01 MB**，不是24H常駐RAM結論 |
| CPU開銷 | 265.625ms CPU time；含啟動的單核心正規化平均**2.56%**。不能宣稱已達長期低於1%目標；P95／穩態未測 |
| 一般權限重測 | 回permission_required／driver_unavailable；證明安裝驅動不代表一般Host自然可讀 |

此CPU的官方Tjunction規格100°C，但短時間讀到86～91°C不足以判定散熱故障或安全無虞；需結合負載與長時間記錄。本輪未改溫控或AI門檻。[Intel規格](https://www.intel.com/content/www/us/en/products/sku/202329/intel-core-i710875h-processor-16m-cache-up-to-5-10-ghz/specifications.html)

## 修正與回歸

- `tools/cpu_sensor_helper/Program.cs`新增有限`--snapshot <GUID>`模式：六筆、每2秒一次，保存固定目錄的試驗報告後關閉自己開啟的Computer；不接受任意輸出路徑或常駐管理命令。一般stdin/stdout模式仍保留。
- `tools/cpu_sensor_contract.py`修正.NET round-trip的7位小數時間與Python3.10相容問題，並新增回歸。原始感測值成功不代表消費端可正確使用：最初Python拒絕時間格式，修正後六筆均通過同一Provider契約。
- `tools/report_cpu_sensor_trial.py`核對request id、大小、時間、數值、明確CPU廠牌，產生歷史試驗摘要與原始檔SHA256；明確live_reading=false、host_integrated=false，不把舊讀值當即時資料。
- 原型探測工具欄位改為`driver_install_performed_by_probe=false`，避免把「這次探測未安裝」誤解成「電腦尚未安裝」。
- 33項相關測試／5.639秒／OK；Ruff通過。GPU為既有契約回歸，不是本輪真實GPU壓力測試。

原始證據（reports不屬於正式發布包）：

- `reports/cpu-sensor-prototype/cpu-snapshot-8168628ca8f342c4b01cadc3270a8b9a.json`
- `reports/cpu-sensor-prototype/cpu-summary-8168628ca8f342c4b01cadc3270a8b9a.json`
- 原始證據SHA256：`8fc80da9a60dd60f0645c5ace4339c5ba41fa8f89717e51e705d76b87d01e841`

## CPU與主機板溫度不能互換

CPU Package／Core來自處理器內部感測器；主機板上的System／Board／PCH／VRM等感測器代表不同部位。即使同一監控工具同時列出，也不能只因單位都是°C就替代。主機板顯示的「CPU」也需確認來源是插座周邊還是直接轉讀處理器；不能僅憑名稱判斷。[Intel CPU感測說明](https://www.intel.com/content/www/us/en/support/articles/000005597/processors.html)

原始LHM清單也有Distance to TjMax；這是距離溫度上限的差值，不是CPU絕對溫度。Provider只按可信Package／Die／Core規則選取，沒有把差值拿來平均。

## 狀態與後續界線

**passed：這台Intel電腦的真實CPU Package／Core隔離讀取。**

**尚未完成：Dashboard持續CPU溫度、正式Telemetry／歷史採樣整合、AMD／其他Intel筆電、可攜版與SYSTEM包內、24H／休眠恢復、低於單核心1%常駐開銷。**

這次授權不等於建立永久特權bridge。下一階段需以現有SYSTEM Host或受限感測bridge設計接入，保持一般Host不整體提權；新增服務／排程／ACL變更均需獨立審查授權。不得把此次快照永久顯示成Dashboard「目前溫度」。PawnIO驅動依使用者授權保持安裝，不自行解除或影響其他使用它的軟體。
