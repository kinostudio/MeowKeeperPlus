# r107 Xiaomi 隱私模式與 PTZ 座標控制修復覆核

日期：2026-10-18
範圍：repository 修正與隔離測試；不改正式 Host、不重啟、不發出實機馬達／隱私命令。

## 結論

- 隱私操作現在會區分控制失敗、逾時、讀回失敗、已確認沒有改變與讀回確認成功；未讀回目標狀態不回報成功。失敗結果提供「查看本次控制診斷」連結。
- PTZ 位置讀回與絕對定位能力分開。現有實機報告對 `chuangmi.camera.026c02` 記錄 `position_readback_verified=true`、`absolute_position_verified=false`。Go2RTC 回報的 `absolute_move=true` 是實作旗標，並非此機型實體馬達驗收。
- 因此，目前沒有任何 Xiaomi 型號列入絕對定位白名單；座標圖可顯示有效讀回位置，但不能點擊、觸控或鍵盤定位。後端也拒絕未驗證型號的絕對位置命令。方向鍵仍走原本 `move(direction)` → 短脈衝 → `stop` 路徑。
- 方向鍵結果不再只因控制端接受就標為成功；前端依移動前後的真實位置讀回顯示「已確認位置變化」或「尚未確認」。停止未確認維持失敗提示。

## 根因與命令差異

1. `reports/ptz-feedback-fix/source/internal/xiaomi/ptz.go` 的 `buildPTZResponse` 將 `AbsoluteMove` 固定回報為 `true`；`position` handler 會呼叫 `SetPosition` 並記錄 operation 13。這只能證明程式有絕對移動路徑，不能證明相機韌體會按目標座標移動。
2. `reports/ptz-feedback-fix/manifest.json` 的真實設備紀錄明確為：位置讀回已驗證、絕對定位未驗證。`docs/PTZ_POSITION_READBACK_REPORT.md` 也要求未驗證前只保留相對方向能力。
3. 單次方向鍵走 `move`，命令是方向相對移動，接著由服務送停止；座標圖則走 `position(angle,elevation)`／operation 13。兩條命令不可互相推論。
4. 隱私位置保存、隱私移動與恢復依賴可恢復的絕對位置；位置讀回本身不足以保證可以回到原位。因此讀回／絕對移動能力不符時，不保存新隱私位置，也不執行位置隱私動作。

## 修改檔案

- `src/meowkeeper/core/notifications/privacy.py`：隱私狀態轉換分類與失敗白話訊息；命令已接受但讀回失敗／未改變時不回報成功。
- `src/meowkeeper/core/notifications/control_journal.py`：最多保留 50 筆；camera ID 雜湊、欄位白名單及錯誤文字遮罩；關聯操作後的真實讀回。
- `src/meowkeeper/integrations/xiaomi/setup_service.py`：絕對定位型號驗證白名單目前保持空集合；讀回可用不等於絕對定位可用；方向操作與診斷紀錄保留。
- `src/meowkeeper/integrations/xiaomi/miss_privacy.py`：保存／使用位置前要求絕對定位能力已驗證，避免建立無法驗收的隱私位置流程。
- `src/meowkeeper/app/web/api.py`：新增登入保護的控制診斷 GET API；PTZ 讀回與命令接受 operation ID 關聯。
- `src/meowkeeper/app/web/templates/diagnostics.html`：新增最近一次操作的時間、結果、原因與展開技術細節。
- `src/meowkeeper/app/web/templates/live.html`：失敗時顯示診斷連結；座標圖僅在有效位置與絕對定位均驗證時可操作；方向鍵命令與停止路徑保留。讀回及座標移動使用有界逾時。
- `tests/test_camera_control_journal.py`、`tests/test_privacy_service.py`、`tests/test_miss_privacy.py`、`tests/test_xiaomi_miss_ptz.py`、`tests/test_live_ptz_readback.py`、`tests/test_ptz_position_visual.py`：對應回歸。
- `configs/release-regressions.json`：將本故障納入發布回歸門檻。

## 隱私診斷狀態

`confirmed_success` 只有在隱私狀態有明確讀回且符合目標時使用。其餘可能結果包括 `unsupported`、`timeout`、`readback_failed`、`confirmed_no_change`、`failed`。診斷頁顯示最近操作時間、目標、是否送出命令、傳輸結果、讀回結果、是否觀察到變化及安全原因；camera ID 以雜湊保存，不保存 URL、帳密或 Token。診斷 API 需要 Dashboard 登入。

失敗訊息會明確說明「讀目前狀態失敗、命令未送出」、「命令已送出但讀回逾時／失敗」、「讀回與目標不符」或「狀態未改變」，並提供帶有 `operation_id` 的連結至 `/diagnostics?operation_id=…#xiaomi-control-diagnostics`；診斷 API 只回傳該次操作。沒有操作 ID 的一般診斷頁則顯示最近紀錄。不會為了測試而自動重複開啟攝影機。

## 相對方向閉環定位的可行性

可以用「單次相對移動 → 停止 → 讀回座標 → 計算剩餘差距 → 下一次短移動」代替絕對定位 API，但要先驗證此型號的每個方向如何改變水平／垂直讀回座標，並確認位置讀回確實反映實際馬達。每步必須有限時、有限脈衝與最大步數；讀回無效、不前進、反向、超時或停止未確認就立即中止，不能長時間推算馬達運轉。

本輪未加入或啟用此閉環定位。現有報告證明過相對方向命令可用，但未提供這台使用中攝影機四個方向與座標變化的完整校正資料。待使用者在安全畫面位置逐方向實測並讀回後，才能建立每型號校正資料，再開放點位移動。定位仍只能按實際讀回確認，不以目標點動畫當成功。

## 驗收結果

- 定向 unittest：32 項通過；發布故障回歸門檻 51 項通過、0 失敗、0 略過，包含本次新增的 Xiaomi 隱私／PTZ 閘門。
- Ruff：本次修改檔案通過。
- Compileall：本次修改 Python 模組通過；回歸台帳 JSON 可解析。
- 真實攝影機馬達、座標定位及隱私模式：`not_checked`。本輪未送實機 PTZ／隱私命令，沒有新的實機讀回證據。
- 正式 Host／安裝包：未重啟、未覆寫、未建置；不能宣稱 r107 正式 Host 已修復。

## 下一步

在使用者確認的安全畫面上，先逐一按一次上／下／左／右，每次讀取前後實際座標並確認停止；記錄每方向座標增減、是否有變化與停止結果。只有四方向映射、短步進與停止均有真實證據後，才評估接線受限的相對閉環定位；若其中一軸無可靠位置讀回，就維持該軸與座標圖停用。
