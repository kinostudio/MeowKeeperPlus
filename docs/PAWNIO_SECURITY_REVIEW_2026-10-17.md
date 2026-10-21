# PawnIO安全審查與下一階段授權邊界

日期：2026-10-17（Asia/Taipei）。範圍是下載與靜態核對，不是驅動安裝或完整安全認證。

**後續更新**：使用者已明確授權安裝與一次管理員試驗，UAC核准後PawnIO2.2.0已安裝，真實Intel CPU Package讀取通過。[驗收與剩餘界線](CPU_TEMPERATURE_REAL_TRIAL_2026-10-17.md)。本文先前「被阻擋／未安裝」段落為歷史，不再代表現況。

## 實測結果

- 官方來源鏈：pawnio.eu → namazso/PawnIO.Setup → Release **2.2.0**。GitHub API所列發布時間為2026-03-15T15:49:08Z。
- installer共**3,410,960 bytes**，SHA256 **1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032**，與官方Release asset digest相同。
- Windows Get-AuthenticodeSignature結果 **Valid**；FileVersion **2.2.0.0**。
- 簽署者 `E=admin@namazso.eu, CN=namazso.eu, O=namazso, L=Debrecen, C=HU`；憑證thumbprint **F380DCC9F706E2756A5047B832FFE719E1BC35F5**；時間戳簽署者為Microsoft Public RSA Time Stamping Authority。
- 自動靜態審查工具：`tools/audit_pawnio.py`。只下載固定官方檔、檢查hash、呼叫唯讀簽章cmdlet，**不執行installer**。證據在 `reports/cpu-sensor-prototype/pawnio-audit/signature.json`；未放入dist。
- Windows PowerShell子程序無法載入Security module，工具改優先使用現有pwsh；未修改ExecutionPolicy、系統PATH或使用者設定。已驗證工具本身可重跑，快取仍先檢查hash。

## 重要權限發現：裝完也不一定能讀

PawnIO **2.2.0 source tag**對應commit `5cdf470831fdfff3f7f1d06363ca6b230f3bf35a`。其 `PawnIO/PawnIO.inf.in:39` 裝置ACL為 `D:P(A;;GA;;;SY)(A;;GA;;;BA)`，預設只允許SYSTEM與Administrators。官方Release也明示：可開放非管理員，但不建議。

所以「只安裝驅動→一般權限Host自然就能讀」是錯誤假設。不得為此把硬體裝置ACL開給Everyone、把整個Host改成管理員，或選擇Unrestricted版。

原型已補強：偵測安裝登錄後，以OPEN_EXISTING開啟PawnIO裝置預檢；拒絕存取回permission_required，其他無法開啟回unavailable；沒有IOCTL、驅動安裝或ACL變更。這只檢查handle可否開啟，不能證明感測器讀取成功。

## 授權與安全判斷

- PawnIO driver為GPL2或後續版本，另有獨立模組僅透過device IOCTL通訊的例外；模組／其他依賴仍有各自條款。不能解讀成可以忽略GPL或所有元件任意閉源散布。本次不重新散布驅動，未複製其程式碼到產品。
- 只考慮官方數位簽章受限版。官網另提供Unrestricted版本選項，不採用，也不啟用測試簽章、不關Secure Boot／Defender／核心隔離。
- 有效簽章證明檔案與憑證鏈核對成功，**不證明無漏洞或取得安全保證**。它是能讀取／控制硬體的核心驅動，不是普通Python依賴。
- installer PE資源含壓縮payload；本輪未完整解包核對所有內含sys／cat的個別簽章。安裝後driver實際版本／簽章、Defender／SmartScreen、重開機影響、解除安裝與其他軟體共用狀態均not_checked。

## 接續方案

1. 需使用者**另行明確授權安裝官方PawnIO 2.2.0受限版**。使用者自行執行並核准Windows UAC／安裝條款；代理不得自行核准或靜默安裝。
2. 先只做一次隔離的有權限CPU Helper測試，不提升正式Host、不改GPU或AI門檻。取得Package／Core真實值後量測，再決定Phase2；不保證裝完就成功。
3. 一般權限長期運作若需要特權感測bridge，必須另設安全設計與範圍：固定受信helper、驗證呼叫者、本機受限IPC、單一實例、窄白名單、完整清理與授權。**此特權bridge尚未實裝／授權**，不能偷偷新增服務／排程工作。
4. SYSTEM安裝版需獨立驗證裝置存取；不能因SY在ACL就宣稱所有CPU可讀。可攜版不得假設以管理員執行。

本輪狀態：下載hash／安裝器Authenticode／原型編譯與退出passed；CPU讀取仍unsupported/driver_missing；驅動安裝、特權bridge、真實溫度、24H與正式包均not_checked。

## 後續實作與實際阻擋（同日）

- 使用者要求自行評斷並以CPU溫度成功為驗收；代理嘗試以正式UAC開啟已核對的官方安裝器，**安全審查在CreateProcess之前拒絕**，理由是尚無對「安裝此核心驅動」本身的明確批准。沒有啟動installer，沒有待核准的UAC，也不得改用間接執行繞過。
- 真實授權執行邊界核對為`aero15\aaron`，目前非提升的Administrator程序；PawnIO服務／安裝登錄未找到，hash再次一致。sandbox帳號不等於此邊界。
- 新增`tools/prepare_cpu_sensor_dependencies.py`，核對官方LHM0.9.6 ZIP的SHA256 `086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001`後，只抽取白名單22個library DLL，不抽取或執行GUI／installer。新增DLL共3,845,176 bytes，沒有新Runtime；報告`reports/cpu-sensor-prototype/dependencies.json`列出逐檔hash。
- Helper增加ReflectionOnlyLoadFrom的中繼資料檢查。實際LHM DLL還參考BlackSharp.Core，不能只看NuGet nuspec就宣稱依賴完整；已補該庫。其餘未帶入的名稱限明確Framework參考，名稱閉包不代表CPU執行／所有native依賴已驗證。
- 重新編譯實際Helper並探測仍為driver_missing；83.86ms是單次預檢與退出時間，不是CPU感測常駐效能。來源與回歸檢查繼續執行，真實CPU測溫仍未通過。
- 本次29項相關測試／5.720秒通過；Ruff與compileall通過。未重新執行整個應用全套測試或修改GPU感測來源。
- 下一步需要使用者明確批准「安裝官方PawnIO2.2.0受限版核心驅動，並執行一次管理員隔離CPU測溫」，UAC由使用者親自確認；不包含Host升級、常駐特權bridge或放寬裝置ACL。

## 官方證據

- [官網：版本與風險](https://pawnio.eu/)
- [2.2.0 Release](https://github.com/namazso/PawnIO.Setup/releases/tag/2.2.0)
- [固定2.2.0來源之裝置ACL](https://github.com/namazso/PawnIO/blob/5cdf470831fdfff3f7f1d06363ca6b230f3bf35a/PawnIO/PawnIO.inf.in#L39)
- [GPL與IOCTL例外](https://github.com/namazso/PawnIO/blob/5cdf470831fdfff3f7f1d06363ca6b230f3bf35a/README.md)
