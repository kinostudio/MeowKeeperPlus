# CPU溫度整合：盤點、隔離原型與停止決策

日期：2026-10-17（Asia/Taipei）。本次不建置、不修改既有dist、不升級正式Host、不提權、不安裝驅動。

**後續更新**：上述為最初原型階段範圍。使用者稍後已明確授權並完成PawnIO安裝；真實i7-10875H隔離試驗已讀到六筆CPU Package，詳見[真實硬體驗收](CPU_TEMPERATURE_REAL_TRIAL_2026-10-17.md)。下方driver_missing為歷史階段結果，不是目前安裝狀態；正式持續整合仍未完成。

## 1. Phase 0：八項現況回答

| 問題 | 實際盤點 |
| --- | --- |
| GPU溫度從哪來？ | `core/analytics/system_telemetry.py::gpu_sample()`執行既有nvidia-smi，逾時2秒，單一可辨識NVIDIA裝置才回傳；多GPU不任選一張。未修改此函式。 |
| 是否統一Telemetry？ | ResourceRecorder背景每60秒採樣GPU／CPU使用率與可用溫度，寫入resource-history.db；`/api/v1/analytics/resources`提供歷史，Telegram使用相同history摘要。AI裝置的即時保護另有既有read_gpu_sample路徑；本次都不改寫。 |
| CPU使用率？ | psutil.cpu_percent；應用用量另由facade程序樹蒐集。 |
| CPU溫度為何缺？ | 現有cpu_temperature使用psutil.sensors_temperatures；本機Windows實測該API不存在。不能因此判斷CPU沒有實體感測器。 |
| 包內有.NET嗎？ | vendor／runtime未找到.NET部署資源。開發機dotnet --list-runtimes只有7.0.5、--list-sdks空白。Windows Framework csc可執行，本次原型由它編譯；不能假定每台目標機具備Framework4.7.2+。 |
| 既有CPU相關依賴？ | 上述psutil方法；未找到既有LHM部署。ResourceHistory已有nullable cpu_temperature_c，無資料不進平均與峰值。 |
| 是否影響降階？ | core/inference/adaptive_analyzer與adaptive_policy已有CPU／GPU負載、溫度、延遲、記憶體壓力控制；Windows CPU溫度目前可能為None。新增來源前需確認定義，這次不變更任何YOLO FPS或門檻。 |
| 如何保持GPU契約？ | 未來只增加CPU結構化狀態／來源欄位；有效新CPU樣本投影至既有nullable欄位，GPU欄位與nvidia-smi完全保留。Phase 1未通過前不接正式路徑。 |

程序生命週期：ResourceRecorder目前隨FastAPI lifespan啟停，不是每位觀看者一份；停止監控但保留Dashboard時recorder仍在。未來CPU helper必須額外遵守監控停止的清理要求，不能直接沿用recorder存在就常駐。Win32_SystemDriver盤點在sandbox被拒絕；不能宣稱已查清全機驅動列表。

## 2. 方案比較（不是猜測數字）

| 項目 | A 保留現況 | B 精簡LHM Helper | C 完整GUI |
| --- | --- | --- | --- |
| 新增壓縮／解壓 | 0／0 | 隔離最小檔案集合368,122／1,258,547 bytes；**不是完整可部署依賴閉包** | not_checked，不納入預設 |
| 新增Runtime | 無 | 本次0 bytes，使用既有.NET Framework；framework-dependent需明確檢查4.7.2+；self-contained另需.NET支援版本，大小not_checked | 依上游對應Framework／現代.NET，未量測 |
| 管理員 | 不新增 | 不要求Host提權；驅動安裝與裝置存取權需獨立審查，未授權／未執行 | 部分感測器需權限，未實測 |
| 核心驅動 | 不新增 | 0.9.6 Intel MSR路徑依賴PawnIO；本機無安裝登錄，原型停止，不自動安裝 | 同一硬體存取風險，不能用GUI規避 |
| 常駐RAM | 無新增 | not_checked（未進入正常感測常駐） | not_checked |
| 平均／P95 CPU | 無新增 | not_checked（未取得感測器） | not_checked |
| 啟動 | 無新增 | 單次helper啟動＋驅動預檢＋退出142.37ms；非正常感測啟動耗時／P95 | not_checked |
| Intel／AMD | Windows可信CPU溫度缺口仍在 | 上游支援候選；本機Intel未取得溫度、AMD實機not_checked | 上游支援候選，不等於本專案實測 |
| 授權／風險 | 無新項 | MPL2.0＋第三方依賴／PawnIO審查，未達交付門檻 | GUI與更多依賴面，不採用 |
| 故障隔離 | 原有 | 獨立子程序、stdin/stdout、父程序逾時與識別驗證，尚未接Host | 額外GUI生命週期，不符合精簡目標 |

本次NuGet完整下載7,161,119 bytes含多種TFM／架構；不是把7MB都塞進可攜包。壓縮數字為exe、net472 x64 DLL、nuspec、LICENSE、THIRD-PARTY-NOTICES五個檔案的ZIP Deflate實測；**缺其餘相依時不能冒稱發布容量**。正式包增量目前為0（未修改包）。

## 3. Phase 1：實際原型

- `tools/cpu_sensor_helper/Program.cs`：Windows Framework獨立console helper，僅CPU、無HTTP／網路埠／GPU查詢，不啟動服務、不提權；先檢查PawnIO登錄，缺失就回unsupported，絕不嘗試安裝。stdin只接受有GUID的sample或stop，命令上限256字元；JSON只回白名單感測器資料，最多128個，無原始例外文字；EOF／stop時關閉自己開啟的Computer。
- `tools/probe_cpu_sensor.py`：從官方NuGet取固定hash、選net472 x64 DLL、使用絕對csc路徑編譯到reports；shell=False、編譯30秒／原型10秒逾時、檢查exit code／識別碼／回應長度。它不是終端使用者安裝工具。
- `tools/cpu_sensor_contract.py`：尚未匯入Host的純資料契約。Intel Package→Die→明示Core；AMD Tdie／CCD(Tdie)，拒絕未確認Tctl；同順位取最高，保留全部候選來源。null、非有限、錯誤／過期不回溫度，不把ACPI當CPU。超過15秒為stale，未來時間／無時區拒絕。
- `tests/test_cpu_sensor_prototype.py`：Intel／AMD模擬語意、單核心、null／NaN／越界／ACPI、過期、權限、崩潰／逾時／錯誤回應、GPU獨立契約等。

執行：`.venv\Scripts\python.exe tools/probe_cpu_sensor.py --prepare`（工程原型命令，不是給一般使用者自行建置的交付流程）。

實際回應：`status=unsupported, reason=driver_missing, sensors=[]`；沒有聲稱取得CPU溫度。原型正常退出後Get-Process未找到MeowKeeperCpuSensor殘留。回應紀錄：`reports/cpu-sensor-prototype/result.json`。

## 4. 供應鏈與版本

- 官方NuGet：LibreHardwareMonitorLib **0.9.6**，nuspec repository commit **3d331e3370efb858411f19511373eff65a218701**。
- nupkg SHA256：`60db5e0d9135f4658b385cae139d3b9eaeac0a8e867d322d20b5dbe4c4039197`。
- 選取DLL SHA256：`6ebc194316536ba61af5be24508ad9fcbb2ecc685e716c12e787c79530f66bf0`，1,203,200 bytes。
- 原始DLL未修改；MPL2.0與上游第三方聲明已留在隔離reports目錄，未放進dist。供應鏈審查未完成，不能只憑hash當成安全認證。
- net472直接依賴：DiskInfoToolkit1.1.2、HidSharp2.6.4、RAMSPDToolkit-NDD1.4.2、System.Management10.0.2、System.Memory4.6.3、System.Threading.AccessControl10.0.3。**本次未安裝／閉包驗證全部依賴**；Driver缺失在載入library之前停止。
- 0.9.4的Ring0.Open存在自動Extract／Install／Delete／重裝路徑，不能套用舊版範例。0.9.6 IntelMsr改用PawnIO，讀取裝置但不由該helper安裝驅動；PawnIO版本、簽章、授權、Defender／SmartScreen、SYSTEM裝置ACL均not_checked。

官方依據：[NuGet0.9.6](https://www.nuget.org/packages/LibreHardwareMonitorLib/0.9.6)、[0.9.6 IntelMsr](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.6/LibreHardwareMonitorLib/PawnIo/IntelMsr.cs)、[PawnIo](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.6/LibreHardwareMonitorLib/PawnIo/PawnIo.cs)、[0.9.4 Ring0風險](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.4/LibreHardwareMonitorLib/Hardware/Ring0.cs)、[授權](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.6/LICENSE)。

## 5. 驗收矩陣與停止條件

| 範圍 | 狀態 | 結果 |
| --- | --- | --- |
| Phase 0盤點 | passed | 已回答八項，列出實際契約與限制 |
| Phase 1編譯／IPC／正常退出 | passed（isolated） | 真實Windows子程序執行、無殘留、無安裝驅動 |
| Intel取得CPU Package | failed（real_hardware preflight） | 未取得：driver_missing；不是不支援Intel CPU的證據 |
| AMD感測器選擇 | passed（isolated）／not_checked（real_hardware） | 模擬Tdie／Tctl分流，不冒充AMD實測 |
| 契約與GPU回歸 | passed（isolated） | 26項相關測試／5.549秒／OK；Ruff與compileall通過；既有GPU程式未改 |
| Phase 2正式Telemetry／Dashboard | not_checked／未實裝 | 遵守Phase1失敗停止正式整合；未新增假溫度卡片或API |
| Phase 3效能／24H／休眠恢復 | not_checked | 未具備正常感測來源；未測平均／P95 CPU、常駐RAM、睡眠恢復、24H |
| 多Dashboard／停止監控清理 | not_checked | helper尚未接Host，不因觀看者啟動；正式清理時機仍需實裝 |
| package_runtime／SYSTEM／可攜版 | not_checked | 未建置、不修改已交付包 |
| 真實GPU／攝影機／Telegram同時工作 | not_checked | 本次只有相關契約回歸，未動正式Host；不可當實機並行效能驗收 |

## 6. Phase 4決策與下一步

同日後續靜態審查已完成官方下載與安裝器簽章核對，另補Driver裝置開啟權限預檢；見[PawnIO審查與授權邊界](PAWNIO_SECURITY_REVIEW_2026-10-17.md)。仍未安裝驅動，尚未取得真實CPU溫度。

**現在不建議納入正式候選包。** 原因是尚未證明可信真實CPU溫度，且依賴／驅動與效能未過門檻。這是規格要求的停止條件，不是已完成全部整合。

下一步需先審查PawnIO官方來源、版本／簽章／驅動風險與目標機已安裝狀態；若確需安裝，必須取得使用者另外明確授權並走正式UAC，不能為完成任務自行安裝。也可改用已存在、可驗證來源的CPU感測資料，不能以ACPI或推估替代。

取得可靠真實樣本後，才建立單一背景Provider（5秒採樣、15秒過期、2～30秒配置、單次3秒逾時、失敗退避），接入既有nullable歷史與授權診斷API；UI區分available／unavailable／permission_required／unsupported／stale／error，提供來源與白話原因。CPU觀測先不改AI保護門檻。需補原型完整依賴閉包、helper hash／程序所有權、無限輸出防護、停止監控／完整退出清理及24H測試，才能考慮候選發布。
