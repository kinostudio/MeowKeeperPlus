# 受限 CPU 測溫工作（2026-10-19，尚未實機部署）

一般帳號即使裝好 PawnIO，也不保證能讀取；不能直接把 Host 切成 SYSTEM，否則可能讀不到原帳號的小米／Telegram 憑證。使用者本輪明確同意受限測溫工作；UAC／官方授權條款仍由使用者親自確認。

## 接線與安全邊界

- 明確同意後 `Manage-CpuTemperature.ps1` 準備官方受限驅動，再呼叫 `Manage-CpuSensorTask.ps1`。
- 固定 action：`Program Files\MeowKeeperPlusCpuSensor\MeowKeeperCpuSensor.exe --broker`，檔案逐一雜湊驗證。SYSTEM／Administrators 可寫，Users 只讀執行。拒絕 reparse 路徑、不安全的既有目錄及不屬於本程式的同名工作。
- 固定 `ProgramData\MeowKeeperPlusCpuSensor` 保存快取與 `request.txt`；Users 只能讀寫 request 的內容，不能刪除／替換檔案或修改 ACL。
- Host 每五秒更新 UTC 請求；SYSTEM 只解析最多128字元、20秒內的時間戳，不接受 PID、程式、命令、路徑或裝置名稱。無網路監聽、任意命令 IPC、高權限 Python，也不放寬 PawnIO ACL。
- 每分鐘啟動一次，迴圈最多55秒；排程 execution limit 一分鐘、IgnoreNew 禁止重疊。無新鮮請求即退出。停止監控清空請求，一般下次五秒檢查退出；若驅動卡住由排程時限終止，不承諾瞬間退出。
- Host 讀取有界 JSON，超過15秒的數值不當作目前溫度。首次等候最長約一分鐘，期間保持請求，不進長退避。
- `-UserHost` 是明確選項：跨 UAC 保存原使用者 SID，Core 用 `user_core` 登入觸發，僅給該使用者 Data 修改權。不等同無人登入的 SYSTEM 24H 模式，不偷偷修改其他安裝預設。
- 系統解除安裝確認描述及固定 action 後移除工作，保留受保護元件供診斷，不移除共享 PawnIO。使用者模式需另用管理員工具移除全機工作。

## 驗收

新增10項涵蓋有效／過期／超大／錯誤契約、首次等待、停止釋放、固定命令、帳號分離、Windows parser與一般權限拒絕 broker。與既有測溫合計23項通過；尚未註冊或執行 SYSTEM 工作。

最新凍結原始碼完整924項／325.159秒通過、2略過；Ruff／compileall與16項最後定向通過。90項強制故障門檻通過。Windows PowerShell 5.1在Aaron執行邊界完成排程／ACL物件建構：週期PT1M、時限PT1M、SYSTEM／固定`--broker`，未註冊、未寫ACL、未啟動感測器。Sandbox的同一CIM建構先被拒絕存取，不把該權限錯誤當程式參數失效。

未驗：實際 ACL／工作註冊與跨使用者安全性、新驅動安裝、目標筆電連續溫度、重開機、完整關閉、解除安裝及24H成本。本機測試不能替代。

r109候選完成21,506檔獨立校驗、27類／80項故障門檻與包內 runtime imports，未簽署或發布。包內刻意不含pip，`pip check`不可執行，沒有加裝。r109不含本次工作，原始碼已改動，必須另建新候選、重跑門檻，不得修改舊manifest或沿用舊gate。
