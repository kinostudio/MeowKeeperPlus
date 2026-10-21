# MVP Phase 3C.4：Windows 單一實例修復與程序 audit

## 結論

已在真實 Windows 程序列表中修正並驗證 Host、Tray 與官方 go2rtc 的單一實例
流程。`Start-MeowKeeperPlus.cmd` 連續執行兩次後，持續服務的 Host、Tray、go2rtc
PID 均不變；第二次只留下短暫的命令列啟動 wrapper，沒有建立第二棵服務樹。

本次只完成 launcher／程序邊界，沒有啟動 Xiaomi profile probe、stream activation 或
P2P 測試。

## 唯讀 audit 與安全停止

`reports/phase3c4/` 保存：

- `processes-before.json`：修復前 0 個 MeowKeeper+ 程序、0 個目標 port，以及
  Task Scheduler／Startup／Registry Run／PID sidecar 的唯讀結果。
- `processes-before-stop.json`、`processes-recovery-stopped.json`：完整命令列驗證後，
  只停止 repository-owned Host、Tray、launcher 與 helper；未使用名稱式 `taskkill`，
  沒有停止其他 Python 專案。
- `processes-after-first-final.json`、`processes-after-second-final.json`：兩次真實
  Windows 啟動的 executable、Parent PID、SessionId、帳號、port owner 與安全命令列。
- `single-instance-result.json`：角色數量、PID 不變、port owner、Xiaomi 尚未啟動的摘要。

修復前 audit 曾找到兩個無父 `cmd.exe`，其完整命令列都指向本 repository 的
`Start-MeowKeeperPlus.cmd`；新增的角色辨識會把這類 wrapper 視為 launcher helper，
只在命令列與 repository path 同時吻合時才納入安全停止。

## 修復內容

1. `scripts/Start-MeowKeeperPlus.ps1` 與 Tray bridge 使用由 repository root 組合的
   絕對 `.venv\Scripts\python.exe`／`.venv\Scripts\pythonw.exe`；不使用 `python`、
   `py`、`pythonw` 或 PATH 搜尋。
2. `src/meowkeeper/process_guard.py` 以 Windows named mutex 加 JSON PID sidecar；
   stale PID 只有在 executable、role 與完整 command line 都可驗證時才可清理，
   不會以 `python.exe` 名稱批次終止。
3. Host 順序為 `/healthz` → Host lock → port owner → runtime；健康 Host 直接重用。
   Tray 取得自己的 lock 後才檢查 Host；已有 Tray 不會再建立 `pythonw.exe`。
4. 主 launcher 現在先建立／重用官方 go2rtc，再啟動 Host 與 Tray。go2rtc 使用
   `MeowKeeperPlusGo2Rtc` 固定鎖於正式 1984／8554；非正式測試 port 使用 port-scoped
   lock 與 repository-local config。
5. go2rtc 啟動前檢查健康狀態與 1984／8554 port owner；已有健康官方程序不會被停止。
   既有 AppData config 只有在逐行確認 API／RTSP port 相符時才唯讀重用，不會以空 config
   覆寫登入 session；不相符的測試 instance 改用 `data/go2rtc/config-<ports>.yaml`。
6. Tray-only 啟動改走同一個 launcher 並傳入 `--no-tray`，避免另起第二個 Host／go2rtc。

## 真實雙次啟動結果

第一次與第二次皆執行：

```text
Start-MeowKeeperPlus.cmd --no-browser --no-monitor
```

| 角色 | 第一次 PID | 第二次 PID | executable | 結果 |
|---|---:|---:|---|---|
| Host | 12820 | 12820 | `.venv\Scripts\python.exe` | 不變 |
| Tray | 26128 | 26128 | `.venv\Scripts\pythonw.exe` | 不變 |
| go2rtc | 35356 | 35356 | `vendor\go2rtc\v1.9.14\go2rtc.exe` | 不變 |
| 第二次 launcher wrapper | 14828 supervisor；33396 invocation | 短暫 wrapper | `.venv\Scripts\python.exe` | 不建立第二棵服務樹 |

Port owner：

- 8000 → Host PID 12820
- 1984 → go2rtc PID 35356
- 8554 → go2rtc PID 35356

計數與安全性：

- `run_host.py` = 1
- `run_tray.py` = 1
- `go2rtc.exe` = 1
- 正式 Python executable 全部位於 repository `.venv`
- 系統 Python 啟動的 MeowKeeper+ = 0
- Task Scheduler matching item = 0
- Startup matching item = 0
- Registry Run／RunOnce matching item = 0
- 報告 `secrets_in_report = false`

## Xiaomi 邊界與 producer

本次啟動使用 `--no-monitor`，只做 Host／Tray／go2rtc lifecycle；未呼叫 Xiaomi source
activation、profile probe 或第二 consumer。因此本次：

- `source_activation_requests = 0`
- Xiaomi producer = 0
- go2rtc `/api/streams` = `{}`

這些 0 只證明 singleton gate 沒有重複呼叫，不是 Xiaomi P2P 成功或失敗證據。

## Session 完整性警告

一次早期驗證暴露並修正了 config port 判斷 bug：`listen: :1984` 形式未被辨識，曾
短暫產生空的 localhost config。後續已改為逐行解析、加入回歸測試，且所有後續正式
啟動都不再覆寫相符的 config。可是原先 AppData config 在該早期驗證中已被替換；目前
本機沒有可安全還原的未遮罩備份，故原 Xiaomi login session **不能視為仍存在**。
本報告不含任何帳號、Token、DID、MAC、密碼或完整 source URL。要繼續 Xiaomi real-device
同步，使用者必須先在官方 go2rtc WebUI 完成一次重新登入；MeowKeeper+ 不會代收密碼或
要求貼出 Token。

## 驗證限制

- 已證明的是 Windows launcher 的真實單一實例與 port ownership，不是 Xiaomi P2P
  allocation、影像首幀或家庭環境長時間穩定性。
- 在使用者重新建立官方 session 前，不得繼續 Xiaomi profile probe／p2pid 測試。
- 完成一次使用者明確的官方登入後，才可重新執行獨立 go2rtc P2P isolation；不得反覆
  自動嘗試帳密。
