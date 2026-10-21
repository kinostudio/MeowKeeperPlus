# Telegram 隱私服務後續完善

本輪僅修改 repository，不變更 SYSTEM 安裝、攝影機或 go2rtc。

## 修正

原本 PrivacyService 只要 Adapter 回傳 `success=true` 就回報成功，沒有獨立讀回
狀態；新增 `read_privacy_state()` 契約，必須回傳 `verified=true` 和 boolean
`privacy_mode`。控制前確認狀態、控制後重新確認目標狀態；不接受送出成功代替
攝影機狀態成功。重複操作不送指令，併發操作安全拒絕，例外不洩漏原始文字。

## 驗證

- `tests.test_privacy_service` 與 `tests.test_telegram_commands`：39 tests 通過。
- 新增 6 個隱私服務測試涵蓋冪等、缺少 reader、無效狀態、未改變狀態、例外與併發。
- 所有相機操作均為測試 Adapter，沒有真實移動攝影機。

## 未完成與版本界線

- 真實 CameraAdapter 尚未接線；不猜測 PTZ 安全位置，也不新增相機 backend。
- `/hide`、`/show` 實機驗收仍未完成。
- repository 現在超前 r39；r39 不包含本輪修正。不得將 r39 安裝視為本輪程式已發布。
- 後續需先確認既有相機具備可讀回的真實隱私能力，再接線並完成實機驗收。
