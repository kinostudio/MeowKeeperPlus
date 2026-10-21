# CPU 溫度持續觀測接線（2026-10-17）

## 範圍與結論

Repository 已接上「背景 CPU 感測 → 共用快取 → 已登入 API → 趨勢分析頁 → 既有 SQLite 資源歷史」。未建置發布包、未安裝、未重啟正式 Host；未更改 GPU 採集、AI 溫控門檻、Windows ACL、服務或排程。

本機一次管理員真實試驗的 CPU Package 86～91°C 是[歷史實測](CPU_TEMPERATURE_REAL_TRIAL_2026-10-17.md)，不是此頁現在的即時值。一般權限的隔離 Host 已實測正確顯示「需要額外權限」；持續真實溫度讀取仍待既有具權限 Host 驗收，不能宣稱長時間測溫成功。

## 資料與生命週期

- CPU 型號取本機登錄／平台資訊，不預設特定型號。Intel 優先 Package，再 Die／Core；AMD 優先 Tdie／CCD。主機板／ACPI／Tctl 不冒充 CPU 實溫。
- 每個 ResourceRecorder 只有一個感測背景工作，預設每 5 秒；API 及每位觀看者只讀共用快取。生命週期判斷不呼叫昂貴的整套系統 metrics。
- 超過 15 秒（自訂間隔時為三倍間隔且至少 15 秒）未更新便標 stale、數值為 null，不保留舊數字為即時、不補零。失敗採 30～300 秒退避。
- 停止監控會在下一次 5 秒週期釋放所屬 helper；Host 結束會停止取樣及自身子程序。不按程序名稱關閉其他軟體。
- 歷史每 60 秒保存當下有效 CPU 溫度；缺失／過期為 SQL NULL。近一小時摘要與圖表是歷史採樣，不是 5 秒完整時間序列或瞬間絕對峰值。
- `MEOWKEEPER_CPU_TEMPERATURE=off` 可關閉本功能；UI 尚無持久化開關／間隔編輯。程式建構介面允許 2～30 秒。

## 安全與交付

- `GET /api/v1/telemetry/cpu-temperature` 與資源歷史沿用既有登入驗證，不開放額外網路埠。
- 本機 helper 使用標準輸入輸出與 nonce、有限大小回應、逾時及關閉；不建立特權 bridge，不自動 UAC，不安裝驅動。
- 啟動前校驗 helper 及依賴雜湊，拒絕額外 DLL、越界路徑或不完整清單。
- `tools/stage_cpu_sensor.py` 從已驗證來源準備 repository 的 `vendor/cpu-sensor`；未含驅動安裝器或新的 .NET runtime。
- 未來建置需明確加 `tools/build_portable.py --enable-cpu-temperature`。預設不含 helper；缺少 helper 會顯示不可用，不使 Host 失敗。依賴 .NET Framework 4.7.2+，須按目標 Windows 實測。

## 修改位置

- `src/meowkeeper/core/analytics/cpu_sensor_contract.py`：共用感測語意與新鮮度契約。
- `src/meowkeeper/core/analytics/cpu_temperature.py`：來源、helper 與背景快取。
- `resource_history.py`、`core_controller.py`、`web/state.py`：監控生命週期與歷史接線。
- `web/api.py`、`templates/analytics.html`、`static/js/resource-history.js`：登入保護與目前值／歷史顯示。
- `tools/cpu_sensor_contract.py`：舊試驗工具改用共用契約。
- `tools/stage_cpu_sensor.py`、`tools/build_portable.py`：可選交付。
- `tests/test_cpu_temperature_monitor.py`、`configs/release-regressions.json`：快取、過期、權限、生命週期與範圍回歸。

## 本次隔離 UI 證據

- Edge 桌面：CPU 型號、需要額外權限、最近檢查、無有效歷史、感測來源及共用 5 秒說明可見。
- 390×844 目標視窗：實際內容可用寬度 375，scrollWidth=clientWidth=375，沒有橫向溢出。桌面兩者均 1684。
- [桌面截圖](../reports/cpu-telemetry-permission.jpg)、[手機寬度截圖](../reports/cpu-telemetry-mobile.jpg)。這是隔離 Host，不是真實監控部署畫面。
- 測試伺服器僅綁 loopback、使用合成帳號／獨立暫存 DB，驗收結束後已關閉；未啟動攝影機 ingest。

## 尚未通過的產品門檻

1. 既有具權限 Host 下持續真實取樣、24H 穩定性及穩態 CPU 占用。
2. AMD、其他 Intel 世代、無驅動／不支援機器與包內 runtime 實測。
3. CPU 使用率並列、有效採樣覆蓋率、超過門檻持續時間與硬體規格上限的通用 UI。
4. 主機板分部位溫度目前未接入；不以其代替 CPU 溫度。
5. 不因本輪接線改變既有 AI 回退的感測來源或溫控設定。

## 驗證結果

- 完整測試：830 項／273.130 秒／OK，輸出 `reports/cpu-continuous-unittest-final.txt`。
- CPU／感測契約／試驗報告／歷史／既有系統遙測：34 項／6.872 秒／OK。
- 強制故障回歸：12 類、24 項 passed，無失敗或略過；包含本輪 CPU 共用快取事件。
- Ruff、compileall、`node --check resource-history.js`：passed。
- 第一次完整測試 829 項有一項取消工作 fixture 競態：替身自行等待 2 秒後返回，繁忙時取消尚未抵達即已完成。改為明確釋放事件與 30 秒故障上限，不修改 production 取消語意；相關 17 項及上述完整重跑通過。
- 隔離伺服器與 helper 殘留檢查：18952 無 listener、無 MeowKeeperCpuSensor 程序。未建包／部署／執行本版秘密掃描；不得將測試通過等同於真實長期硬體或發布驗收。
