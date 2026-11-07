# 真正的檔案差量更新

## 契約

- `schema=2 / kind=file_delta` 的 Ed25519 簽章清單綁定官方 repository、基準版本、基準 build manifest SHA-256、目標版本、差量 ZIP 雜湊及逐檔新增／修改／刪除清單。
- 下載 ZIP **之前**，核對已安裝基準與每個檔案的大小、SHA-256、路徑及重新解析點。版本或內容不同即停止；沒有自動完整包回退，也不以忽略差異方式更新。
- ZIP 只包含實際有變更的檔案及目標 build manifest。runtime、CUDA、FFmpeg、模型未變時，不傳送這些檔案。這是檔案差量，不是二進位區塊差量；大型依賴本身變更時仍需下載該檔案，發布摘要必須列出。
- 本機從既有 App 重用未變檔案，建立完整候選目錄，再驗證目標 manifest；同磁碟優先硬連結，不能連結才複製。不就地修改執行中的 App，也不把 Data 放進更新包。
- 網路下載量縮小不代表磁碟／驗證時間歸零；仍需完整候選、舊 App 與持久 Data 快照的空間。換版需要短暫停止監控，不是零停機。

## r116 的一次性過渡

舊版更新器不認識新協定，因此提供對應目標版本的小型 `MeowKeeperPlus-r119-delta-bootstrap.pyz`。使用者在主機本機管理員 PowerShell 核對其固定 SHA-256，使用**現有內附 Python**執行；不需要安裝 Python、pip 或重新下載 runtime。

過渡工具只接受固定官方版本網址，使用已安裝的官方公鑰驗證差量簽章；先驗基準，再下載 ZIP。已驗證的新 worker／驗證器置於 Administrators／SYSTEM 保護的 Updater 目錄，只有使用者明確 `--apply` 才呼叫受控換版流程。不自行觸發 UAC。

過渡工具的更新要求、差量封存及 verifier 也全部位於受保護的 Updater 子目錄；不以管理員權限向一般帳號可寫的 Data 更新暫存複製檔案。固定 SYSTEM 的日常 Dashboard 路徑仍會先複製至受保護目錄並重新驗證。

r116→r118 與 r116→r119 包的基準為官方 r116 加上一份已知、已公開的 `src/meowkeeper/app/web/state.py` 修正；該檔案的確切大小與 SHA-256 另列於簽章 `base_overrides`。這不是允許任意修改：其他內容或未包含該修正的 r116 會拒絕套用。基準不符需另外製作對應的簽章差量，不自動下載完整包。

後續版本由既有固定 SYSTEM 更新工作處理新協定；Dashboard 仍須本機登入、CSRF 與明確中斷確認。不接受任意下載網址、命令或 verifier 路徑。

## 失敗回復

重新使用安全更新交易：停止已驗證的舊程序、備份 Data（含 SQLite 一致性快照）、保存 Core／Tray 工作定義與舊 App，再切換及檢查健康。失敗嘗試恢復 App、Data 與排程；保留失敗現場，不能只恢復程式卻宣稱資料已回復。

受控更新明確跳過選用測溫驅動安裝提示，不在 SYSTEM Session 0 顯示對話框，也不重新安裝驅動；既有測溫背景工作保留。

管理員過渡與固定 SYSTEM worker 共用 Updater 中的獨占檔案鎖；重複執行會在切換前退出，不覆蓋進行中的工作狀態。程序結束釋放鎖，不以殘留檔案是否存在誤判忙碌。

受控交易使用短 `work-xxxxxxxx/a` 與 `App.b-<16碼>`／`App.f-<16碼>` 目錄，保留完整 request ID 的狀態關聯；撞名即停止，不覆寫。Python 深層下載準備使用 extended path。worker 在停止監控前檢查目標樹的傳統 Windows 路徑長度，超出即停止，不修改系統長路徑登錄設定；舊式備份 receipt 仍可讀取復原。

## 驗收邊界

`tests/test_delta_updates.py` 驗證僅變更檔案、簽章／基準／路徑／ZIP 防護、已知修正基準、無完整包回退、下載前拒絕及 worker 重建；`tests/test_hotfix_transaction.py` 驗證隔離檔案與 SQLite 回復。發布前另做真實候選包重建、逐檔完整性與包內 HTTP smoke。

上述不等於正式 Host 的 SYSTEM 排程、UAC、真實停止／啟動、斷電恢復或正式資料回滾驗收。正式安裝由使用者執行，未實測項目必須標記 `not_checked`。

## r119 更新命令

[r119 累積差量候選](https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r119) 包含 r118 的修正與 Telegram 接收器退避恢復。完整 1,086 項／回歸 185 項零略過、真實套件重建、原 r116 runtime 準備與隔離 HTTP 啟動通過；三個公開資產重新下載後簽章、SHA-256 與內容核對通過。ZIP 1,374,716 bytes、31 個變更檔、零刪除、無 runtime／vendor／models 變更下載。

只適用本頁所述的精確 r116 基準，無需先安裝 r118。請由使用者在要更新的電腦開啟**管理員 Windows PowerShell**執行；會短暫停止監控，仍需本機重建／快照磁碟空間。不會自動觸發 UAC，開發流程不代為安裝。正式 SYSTEM 更新、正式資料回滾及斷電恢復仍待實機驗收。

```powershell
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$hotfixTool = Join-Path $env:TEMP 'MeowKeeperPlus-r119-delta-bootstrap.pyz'
$pythonPath = Join-Path $env:ProgramData 'MeowKeeperPlus\App\runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '找不到既有安裝版 Python；未開始更新。' }
Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/kinostudio/MeowKeeperPlus/releases/download/r119/MeowKeeperPlus-r119-delta-bootstrap.pyz' -OutFile $hotfixTool
if ((Get-FileHash -LiteralPath $hotfixTool -Algorithm SHA256).Hash.ToLowerInvariant() -ne '8851e71b766ff8c08318fefaf776f7cd15210b5bebe00952ce43ad703c5f1a9b') { throw '過渡工具校驗失敗，停止更新。' }
& $pythonPath -B $hotfixTool --version r119 --apply
if ($LASTEXITCODE -ne 0) { throw '差量更新未完成；請保留程式與備份，不要改抓完整包或重複執行。' }
```

工具先核對簽章與精確基準，再下載差量；不符即停止，沒有完整包回退。不要將 ZIP 直接覆蓋 App，也不要平行執行多個更新。另一台電腦若持續使用同一 Bot 輪詢，仍需排除重複接收端；自動退避不會替你關閉其他程式。

## r118 更新命令

[官方 r118 候選](https://github.com/kinostudio/MeowKeeperPlus/releases/tag/r118) 已通過完整1078／回歸177、實際包重建與隔離 HTTP 啟動驗收；三個公開資產下載與簽章／SHA-256均已核對。差量 ZIP 1,367,346 bytes、30個變更檔，runtime／vendor／models變更檔為零。正式安裝與斷電／正式資料回復尚未驗收。

只適用本頁列出的精確 r116 基準。請在要更新的電腦上，親自開啟**管理員 Windows PowerShell**，貼上以下命令。r116尚不認識差量協定，因此首次使用6.5KB過渡工具；不需要先下載大型新版程式包。套用會短暫停止監控，並需要本機重建與備份空間。

```powershell
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$hotfixTool = Join-Path $env:TEMP 'MeowKeeperPlus-r118-delta-bootstrap.pyz'
$pythonPath = Join-Path $env:ProgramData 'MeowKeeperPlus\App\runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '找不到既有安裝版 Python；未開始更新。' }
Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/kinostudio/MeowKeeperPlus/releases/download/r118/MeowKeeperPlus-r118-delta-bootstrap.pyz' -OutFile $hotfixTool
if ((Get-FileHash -LiteralPath $hotfixTool -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'fddf1f4e94b6df7e403da018d7056d55b6424895ab0e7cf619be452a43373861') { throw '過渡工具校驗失敗，停止更新。' }
& $pythonPath -B $hotfixTool --version r118 --apply
if ($LASTEXITCODE -ne 0) { throw '差量更新未完成；請保留程式與備份，不要改抓完整包或重複執行。' }
```

工具先核對簽章與全檔案基準，再下載差量；不符就停止，沒有完整包自動回退。不要將ZIP直接覆蓋到App，也不要同時執行多個更新。
