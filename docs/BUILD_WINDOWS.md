# Windows 可攜式發布建置

2026-10-17新增：正式 `build_portable()` 會在建立staging／覆蓋舊包前強制執行已知故障回歸；不接受上次結果、skip或失效程式指紋。結果保存於包內 `release_regression_gate.json` 與build_manifest。詳見 [工程回饋閉環](ENGINEERING_FEEDBACK_LOOP.md)；`--dry-run`不代表門檻通過。該修改尚未納入r105；現有包不可補寫假證據。

Tray 完整關閉與右鍵防卡頓改動見 [r87 Tray 報告](R87_TRAY_SHUTDOWN.md)。完整關閉是使用者明確要求停止整個應用，不改變預設未登入持續監控原則。

r86 已納入 [r85 Public UDP 衝突修正 gate](XIAOMI_UDP_FIREWALL_COMPAT.md) 的程式修正；發布及驗收見 [r86 修正報告](R86_XIAOMI_CONTROL_FIX.md)。不得修改既有 r85 manifest，未驗收的遠端 JPEG、UAC 與位置恢復仍不可宣稱通過。

SYSTEM 升級的媒體程序清理與 UAC 驗收，見 [Xiaomi SG／JPEG 與 SYSTEM 升級 SOP](XIAOMI_SG_JPEG_SYSTEM_UPGRADE_SOP.md)。

傳統 Windows PowerShell 若標題出現「選取」，QuickEdit 會暫停主控台輸出，連帶讓安裝器等待子程序；在該視窗按一次 Esc 可恢復原流程。安裝器原始碼現在於執行期間暫停 QuickEdit，結束（包含錯誤）時恢復原 console mode，並在複製後顯示安裝目錄校驗進度。已建好的 r32 不包含此後續修正；勿直接修改已驗證發布包。

可攜版不需安裝：複製完整資料夾至另一台 Windows 電腦，執行 `MeowKeeperPlus.cmd`，會啟動／沿用 Tray，由 Tray 啟動／沿用核心，健康檢查後開啟 Dashboard。重複執行只重新開啟頁面，不新增 Tray。入口使用內附 runtime，不需要 venv 或 pip。進階的核心單獨入口為 `MeowKeeperPlusDashboard.cmd`；Tray 單獨入口為 `MeowKeeperPlusTray.cmd`。SYSTEM 安裝版的 Tray 呼叫既有 `MeowKeeperPlus Core` 排程，不啟動另一個使用者身分的 Host。Tray 選單沒有獨立「結束 Tray」操作；24H 背景核心仍獨立於登入工作階段，異常關閉 Tray 不會停止核心。

Dashboard「進階設定」提供密碼保護的完整 ZIP 備份，包含一般設定、SQLite 事件／回饋、寵物資料／照片／embeddings、事件影片／截圖，以及目前 Host 身分 Windows 憑證庫中的 Telegram 憑證。備份採 scrypt 與 AES-256-GCM，匯入需明確確認並重啟 Host；密碼至少 6 個字元。暫存 ring／preview cache、模型、FFmpeg、Python runtime 與 Xiaomi/go2rtc 登入 Session 不放入備份。使用環境變數提供的 Telegram Token 不會被匯出。舊版設定 JSON 仍保留作相容用途，但不包含事件、寵物或影片。請妥善保管備份密碼與檔案；另一台電腦上需重新完成 Xiaomi 登入與實機來源檢查。

Dashboard 的「喵喵守護者+ 整體用量」將可辨識的 Host、Tray、go2rtc 與內附 FFmpeg CPU／記憶體合計展示，與「整台電腦用量」分開。這是觀測合計，不會把多個必要的 Windows 程序物理合併為一個 PID。

產品決策（2026-10-03）：優先保留「使用者未登入時，SYSTEM Core 仍持續監控」。因此不為了讓 Windows 工作管理員只顯示一個處理程序而把 Core 與互動式 Tray 強行合併；go2rtc、FFmpeg 亦維持可獨立重啟的子程序。工作管理員會顯示多個 PID，Dashboard 的整體用量只作為彙總觀測，不宣稱單一 PID。可攜版未註冊 SYSTEM 排程時，關閉整個 Host 即停止監控；正式 SYSTEM 安裝版才具備未登入持續運作的條件。

MeowKeeper+ 的終端使用者不應安裝 Python、建立 venv、執行 `pip`、安裝 FFmpeg、
下載模型或填寫模型路徑。發布包由開發／建置機準備完整 runtime 後產生。

## 建置者前置條件

建置者必須明確準備：

- 官方 Windows embedded Python 3.10+ runtime，放在 `runtime/python` 或由
  `--python-runtime` 指定。
- 該 runtime 內已準備本專案 runtime 套件（numpy、opencv-python、ultralytics、lapx、
  ONNX Runtime、torch、torchvision、固定版 yt-dlp、yt-dlp-ejs 等）。`build_portable.py` 只驗證，
  不會偷偷執行 pip。
- 固定版 `yt-dlp` 除 Python 套件外，還須由建置者安裝命令列入口
  `runtime/python/Scripts/yt-dlp.exe`；正式建置會實際執行 `--version` 並核對版本。
- MSE 連續預覽必須內附 `websockets==15.0.1`。建置以內附 Python 匯入
  `websockets.asyncio.client` 和 Uvicorn WebSocket server transport 並核對版本；
  不能用開發 `.venv` 的成功結果替代。HTTP 健康與檔案雜湊通過都不能證明 WebSocket 可用。
- `vendor/ffmpeg/bin/ffmpeg.exe`、`ffprobe.exe` 與其授權檔。
- `vendor/javascript/node.exe` 與 `LICENSE`，供
  yt-dlp JavaScript challenge 解譯；終端使用者不需另行安裝 Node。
- `models/yolo11n.pt` 與 `models/model_manifest.json`，且 SHA-256 通過啟動驗證。

可在建置機執行 `python tools\prepare_portable_runtimes.py`，自動從 Python.org 與
Node.js 官方固定版本取得 Windows x64 ZIP、驗證固定 SHA-256，放入
`runtime/python` 與 `vendor/javascript`。此工具不修改系統 Python／Node／驅動／CUDA，
且目標目錄已存在時拒絕覆寫。目前固定 Python 3.13.15、Node 22.23.2；
版本更新須先審核官方來源、相依套件 wheel 與授權。官方 Python embedded 包不含
本專案套件；`prepare_portable_runtimes.py` **不會因此宣稱發布包已完成**。
建置工具的 HTTP 依賴已列於 `pyproject.toml` 的 `build` extra，僅由建置環境使用。
`runtime/` 與 `vendor/javascript/` 為建置產物，已排除 Git 提交。

開發期間不得未經確認修改 Python、驅動或 CUDA。若需要準備新的 runtime，應由建置者
以已審核的離線／固定版本流程處理並留下版本記錄。

## 可重複命令

先只驗證，不複製約 500 MB 的 FFmpeg：

```powershell
python tools\build_portable.py --python-runtime runtime\python --dry-run
```

確認通過後建立包：

```powershell
python tools\build_portable.py `
  --python-runtime runtime\python `
  --output dist\MeowKeeperPlus-portable
```

若輸出目錄已存在，必須明確加入 `--force`；工具不會默默覆寫。預設只包含 MVP 必要的
YOLO11n；DINOv2 身分模型仍屬 experimental，只有明確指定
`--include-experimental-models` 才會放入。指定後會同時納入已驗證的官方 DINOv2 權重與
固定 commit 的 hub source cache，讓寵物身分特徵可在沒有外網下載的正式包中建立。

正式建置不只檢查套件資料夾是否存在，還會用內附的 `python.exe` 實際匯入必要套件；
缺少或無法匯入時拒絕建置。2026-09-21 的本地可攜包已完成依賴匯入、Dashboard
啟動及真實圖片 CPU 推論；Windows 10／11、MX250、真實 Telegram 與 12–24 小時
運作仍待驗收，不能稱為對外發布版。

建置機如已在 embedded runtime 安裝 `yt-dlp==2026.7.4` 套件、但缺少 CLI，可由
repository `.venv` 的 pip 對**內附 runtime** 執行：

```powershell
.venv\Scripts\python.exe -m pip --python runtime\python\python.exe install --no-deps --force-reinstall yt-dlp==2026.7.4
```

此命令僅供建置者使用，不修改系統 Python，也不要求終端使用者執行 pip。

## 包內容與啟動

包內應包含：

- `runtime/python`：Python runtime 與必要套件。
- `vendor/ffmpeg`：FFmpeg／FFprobe 與授權資料。
- `vendor/javascript`：只含 Node `node.exe` 與 `LICENSE`；不複製 npm 工具鏈。
- `models/yolo11n.pt`、`models/model_manifest.json`。
- `src`、`tools`、繁體中文文件與 `MeowKeeperPlus.cmd`、`MeowKeeperPlusDashboard.cmd`、
  `MeowKeeperPlusTray.cmd`、`Install-MeowKeeperPlus.cmd`、`Uninstall-MeowKeeperPlus.cmd`。
  小米攝影機 UDP 診斷／修復工具隨包提供於 `scripts/Xiaomi-Firewall-Wizard.cmd`；預設只診斷，使用者明確同意後才透過 UAC 建立限縮規則。完整範圍與復原方式見 `docs/XIAOMI_UDP_FIREWALL_COMPAT.md`。
- `build_manifest.json`：建置日期、模型 hash、每個檔案的大小與 SHA-256。

若使用 `--include-experimental-models`，包內會另外包含官方 PyTorch hub 的空白
`data/torch/hub/trusted_list` metadata。它已列入 manifest；啟動 DINOv2 時不會因此被視為
未知私人資料。其他未列入 manifest 的 `data` 檔案仍會使驗證失敗。

專案原始碼目錄的 `Start-MeowKeeperPlus.cmd`、`Start-MeowKeeperPlus-Tray.cmd` 與
`scripts/Start-MeowKeeperPlus*.ps1` 固定使用開發期 `.venv`，**不得複製進可攜式包**。
發布包只提供使用 `runtime/python` 的執行入口與上述安裝／解除安裝入口，
避免終端使用者誤點無法啟動的開發器。

不應包含 `.env`、Token、RTSP 密碼、YouTube Stream Key、SQLite、私人事件影片、
`data`、`reports` 或測試居家資料。啟動 YOLO 時仍會重新驗證模型 manifest；缺少或損毀
時顯示繁體中文錯誤並停止使用該模型。

## Windows 註冊與回退

可攜包根目錄含有：

- `Install-MeowKeeperPlus.cmd`：驗證全部 build manifest 與模型 hash，由使用者確認 UAC 後，
  將程式放到 `%ProgramData%\MeowKeeperPlus\App`，建立 Core／Tray 排程工作。
- 安裝成功後，開始功能表會建立「MeowKeeperPlus\喵喵守護者+」啟動捷徑。完整關閉後，使用者點此捷徑即可安全啟動／沿用 Core、Tray，健康檢查通過後開啟 Dashboard；不必開 PowerShell 或重跑安裝程式。捷徑會驗證排程工作／啟動檔指向本安裝，避免執行同名的其他工作。
- `Uninstall-MeowKeeperPlus.cmd`：停止並移除這兩個固定名稱的工作及應用檔案；預設保留
  `%ProgramData%\MeowKeeperPlus\Data`。只有明確加上 `-RemoveData` 才會刪除設定、SQLite、
  事件影片與日誌。

若使用者暫時無法核准 UAC，可使用目前使用者模式：

```powershell
Install-MeowKeeperPlus.cmd -CurrentUser
```

此模式安裝到 `%LOCALAPPDATA%\MeowKeeperPlus`，在目前使用者的 Startup 資料夾建立
Core／Tray 啟動檔，不要求管理員權限；不使用這台電腦已拒絕標準使用者建立的排程工作。
它可完成 Dashboard、監控與通知的日常使用，但**不等同**於 SYSTEM 開機前啟動；若要在
尚未登入 Windows 時持續監控，仍須使用預設 UAC 系統模式。解除此模式時使用
`Uninstall-MeowKeeperPlus.cmd -CurrentUser`，資料同樣預設保留。

預設系統模式安裝會檢查目前使用者的 `%LOCALAPPDATA%\MeowKeeperPlus\App\build_manifest.json`，以及目前使用者 Startup 資料夾內精確名稱的 `MeowKeeperPlus User Core.cmd`、`MeowKeeperPlus User Tray.cmd`、`MeowKeeperPlus User Core.lnk` 與 `MeowKeeperPlus User Tray.lnk`。任一標記存在時，安裝會在 UAC 前停止；請先以目前使用者權限執行 `Uninstall-MeowKeeperPlus.cmd -CurrentUser`（預設保留 Data），再重試系統模式安裝。

`-CurrentUser` 解除安裝只會停止 `ExecutablePath` 精確等於 `%LOCALAPPDATA%\MeowKeeperPlus\App\runtime\python\pythonw.exe`，且命令列包含同一 App `tools\run_host.py` 或 `tools\run_tray.py` 完整雙引號路徑的程序；若執行檔路徑或命令列缺失或不符，則不會停止該程序。停止前會以同一 process handle 再核對執行檔路徑與建立時間，最多等待 15 秒並重新掃描；若程序清單、handle、停止、等待或再次確認失敗，或仍有符合程序，解除安裝會在移除任何 Startup、App 或 Data 檔案前中止。初次清單讀取失敗時明確回報零檔案已移除；Data 仍預設保留。

`-CurrentUser -Upgrade` 也採 fail-closed：固定 App 路徑若是 reparse point 便中止；完整安裝會驗證 manifest，若上次升級已中斷而缺少 manifest，則只封存該固定 App 目錄，不直接遞迴刪除。清理程序以舊 App 內附 `runtime/python/python.exe`／`pythonw.exe` 的完整路徑、PID、建立時間與即時命令列驗證；AI multiprocessing worker 的命令列可能不含 `run_host.py`，但其執行檔仍限定在舊 App runtime。先停止非 Host／Tray 的子程序，再停止 Host／Tray；任何資訊缺失、PID 重用、停止逾時或殘留都會在封存 App 前中止，不會停止其他 Python 安裝。`Data` 位於獨立目錄，不參與封存。

安裝腳本不安裝 Python、pip、FFmpeg、驅動或 CUDA，也不寫入任何秘密。
若系統模式安裝命中上述標記，`-WhatIf` 會直接輸出 JSON `status=blocked` 與偵測到的標記路徑，不執行發布包驗證或 XML 產生；未命中時，`-WhatIf` 仍會完成發布包驗證與 XML 產生，但不複製檔案、不註冊工作、不啟動程序：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Install-MeowKeeperPlus.ps1 -WhatIf
```

升級必須明確使用 `-Upgrade`；舊應用目錄必須是固定安裝路徑且不可為 reparse point。
即使目錄缺少 `build_manifest.json`（例如前次刪除中斷），也只會先改名封存；
只有新 App 完成 manifest 校驗後才清理舊備份。若新包複製或校驗失敗，會嘗試恢復舊 App。
資料目錄不受影響。封存改名若因檔案占用或權限而失敗，升級會在遞迴刪除前停止。

SYSTEM `-Upgrade` 在封存舊 App 前，只停止完整執行路徑確認屬於舊 MeowKeeper+ App 的
`python.exe`／`pythonw.exe`（包括 AI multiprocessing 子程序）、go2rtc、內附
`ffmpeg.exe`／`ffprobe.exe`，不按全域程序名稱終止。
停止前會重新核對 PID 與建立時間，命令列不會寫入日誌。無法核實或停止時，
安裝器仍會在刪除舊 App 前中止。這項行為只適用於 SYSTEM `-Upgrade`，不適用一般啟動或診斷。
已產生的發布包不會隨原始碼自動更新，必須重新建置並驗證新包後才會包含此變更。

管理者仍可手動產生只含環境變數名稱、不含 RTSP 密碼的啟動 XML：

```powershell
python tools\create_task_scheduler_xml.py `
  --root C:\MeowKeeperPlus `
  --output C:\MeowKeeperPlus\MeowKeeperPlus.xml
```

工具不會自行註冊工作；只有使用者啟動安裝腳本並確認 UAC 後才會呼叫
`schtasks /Create /XML`。目前已有安裝、更新與保留資料的解除安裝流程，但尚未在
全新 Windows 10／11 主機完成 UAC、開機、登入後 Tray 與重開機驗收，因此仍不得宣稱
已可對外發布。
# Phase 3 發布包補充

可攜式建置除了 headless `MeowKeeperPlus.cmd`，還會產生：

- `MeowKeeperPlusDashboard.cmd`：啟動只綁 localhost 的 FastAPI Dashboard。
- `MeowKeeperPlusTray.cmd`：啟動只呼叫 localhost API 的輕量 Tray。

建置 runtime 必須由建置者事先準備 FastAPI、Uvicorn、Pydantic、Jinja2、python-multipart、
itsdangerous、httpx、pystray、Pillow 與 psutil；`tools/build_portable.py` 只驗證與複製，不偷偷執行 pip、修改 Python、驅動或
CUDA。官方 go2rtc `v1.9.14` Windows x64 binary 已完成 release／授權／SHA-256 審查，
會隨 `vendor/go2rtc` 一起複製到可攜式包；Tailscale 仍不自動安裝或登入。終端使用者不
需要建立 venv、執行 pip 或手動下載模型／FFmpeg／go2rtc。

背景核心與 Tray 可分開產生 Task Scheduler XML（工具只寫檔，不自行註冊）：

```powershell
python tools\create_task_scheduler_xml.py --root dist\MeowKeeperPlus-portable --output reports\core.xml --role core
python tools\create_task_scheduler_xml.py --root dist\MeowKeeperPlus-portable --output reports\tray.xml --role tray
```

`core` 現在指向正式 `run_host.py --no-open-browser`，會在開機時以 SYSTEM 啟動監控與
Dashboard；來源優先使用排程程序可見的環境變數，沒有時讀取安裝資料目錄的
`config.json`。
`tray` 是使用者登入後的互動入口；關閉 Tray 或瀏覽器不會停止 `core`。
已用 Windows Task Scheduler COM 的記憶體內 XML parser 驗證兩種定義，**尚未註冊工作**。

注意：Windows Credential Manager／DPAPI 依執行帳號隔離。以 SYSTEM 啟動的 `core`
不能讀取一般使用者帳號先前保存的 Telegram 憑證；必須在該 Host 的受保護 Dashboard
重新安全設定，或採用明確供 SYSTEM 工作讀取的安全供應方式。僅把使用者環境變數寫在
一般帳號也不會自動傳給 SYSTEM。此跨帳號秘密及實際開機啟動仍待實機驗收，不能把
XML 產生成功當成 24H 發布完成。

安裝腳本會將 Core 的 `config.json`、SQLite、事件、ring、preview 與 Web 管理員雜湊
導向獨立 `Data` 目錄，不再寫入 `App`。SYSTEM 帳號的 Telegram 憑證必須透過
已註冊 Core 提供的受保護 Dashboard 安全儲存，不能把一般使用者的 Credential
Manager 狀態當成 SYSTEM 已可讀取。

安裝腳本啟動 Core／Tray 後，會自動執行 `tools/verify_windows_installation.py`：
要求兩個排程存在、Dashboard `/healthz` HTTP 200、Host=1、Tray=1，且正式 Python
執行檔均來自安裝包的 embedded runtime。驗證失敗會保留安裝與資料以便診斷，
不會偽裝成成功或自動刪除事件。
