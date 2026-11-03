param(
    [switch]$NoPrompt
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Off
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "  [喵喵守護者+] 需要系統管理員權限以升級正式安裝版...    " -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "正在為您申請權限，請在彈出的【使用者帳戶控制】視窗點擊【是】..." -ForegroundColor Yellow
    $argString = "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$PSCommandPath`""
    try {
        $process = Start-Process powershell.exe -Verb RunAs -ArgumentList $argString -Wait -PassThru
        exit $process.ExitCode
    } catch {
        Write-Warning "使用者已取消授權；系統未變更。"
        Read-Host "請按 Enter 鍵關閉此視窗..."
        exit 1223
    }
}

try {
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "          喵喵守護者+  正式最新版本安裝升級精靈            " -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $workspace = "C:\Users\user\Desktop\MeowKeeperPlus-Workspace"
    $installedRoot = "C:\ProgramData\MeowKeeperPlus"
    $installedApp = Join-Path $installedRoot "App"
    $installedData = Join-Path $installedRoot "Data"
    $backupTimestamp = (Get-Date -Format "yyyyMMdd-HHmmss")
    $backupDir = Join-Path $installedRoot "App.upgrade-backup-$backupTimestamp"

    if (-not (Test-Path $workspace)) {
        throw "找不到工作區原始目錄：$workspace"
    }
    if (-not (Test-Path $installedApp)) {
        throw "找不到正式安裝目錄：$installedApp"
    }

    # 1. 停止運行中的 MeowKeeperPlus 排程與程序 (絕不終止當前腳本或 PowerShell)
    Write-Host "`n[1/6] 停止現有排程與運行中的監控程序..." -ForegroundColor Yellow
    schtasks.exe /End /TN "MeowKeeperPlus Core" 2>$null
    schtasks.exe /End /TN "MeowKeeperPlus Tray" 2>$null

    $targetNames = @("python.exe", "pythonw.exe", "ffmpeg.exe", "go2rtc.exe")
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in $targetNames -and
        $_.ProcessId -ne $PID -and
        ($_.CommandLine -like "*ProgramData\MeowKeeperPlus*" -or
         $_.CommandLine -like "*run_host*" -or
         $_.CommandLine -like "*run_tray*")
    }
    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "   已停止背景程序 PID $($p.ProcessId) ($($p.Name))"
        } catch {}
    }
    Start-Sleep -Seconds 2

    # 2. 建立舊版安全備份
    Write-Host "`n[2/6] 建立舊版程式碼安全備份至 $backupDir..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backupFolders = @("src", "tools", "scripts", "models", "configs")
    foreach ($bf in $backupFolders) {
        $srcSub = Join-Path $installedApp $bf
        if (Test-Path $srcSub) {
            Copy-Item -Path $srcSub -Destination $backupDir -Recurse -Force
        }
    }
    Write-Host "   備份完成。" -ForegroundColor Green

    # 3. 設定目錄權限
    Write-Host "`n[3/6] 設定目錄權限 (授權 Users 完整存取)..." -ForegroundColor Yellow
    & icacls "$installedApp" /grant "Users:(OI)(CI)F" /T /C /Q 2>$null

    # 4. 複製最新工作區檔案至安裝版目錄
    Write-Host "`n[4/6] 同步工作區最新修正至安裝目錄..." -ForegroundColor Yellow

    function Sync-CleanDirectory([string]$Source, [string]$Destination) {
        if (-not (Test-Path $Source)) { return }
        if (-not (Test-Path $Destination)) {
            New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        }
        Get-ChildItem -Path $Source -Recurse | Where-Object {
            $_.FullName -notmatch '\\__pycache__(\\|$)' -and
            $_.FullName -notmatch '\\\.git(\\|$)'
        } | ForEach-Object {
            $relPath = $_.FullName.Substring($Source.Length).TrimStart('\', '/')
            $targetPath = Join-Path $Destination $relPath
            if ($_.PSIsContainer) {
                if (-not (Test-Path $targetPath)) {
                    New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
                }
            } else {
                $targetDir = Split-Path $targetPath -Parent
                if (-not (Test-Path $targetDir)) {
                    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
                }
                Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Force
            }
        }
    }

    $foldersToSync = @("src", "tools", "scripts", "models", "configs", "assets")
    foreach ($folder in $foldersToSync) {
        Write-Host "   同步模組: $folder..."
        $srcDir = Join-Path $workspace $folder
        $dstDir = Join-Path $installedApp $folder
        Sync-CleanDirectory -Source $srcDir -Destination $dstDir
    }

    # 同步根目錄 cmd 啟動檔案
    Get-ChildItem -Path $workspace -Filter "*.cmd" -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $installedApp -Force
    }

    # 5. 更新工作排程器 (Task Scheduler)
    Write-Host "`n[5/6] 更新工作排程器 (Task Scheduler)..." -ForegroundColor Yellow
    $installedPython = Join-Path $installedApp "runtime\python\python.exe"
    $xmlTool = Join-Path $installedApp "tools\create_task_scheduler_xml.py"
    $coreXml = Join-Path $installedApp "core_task.xml"
    $trayXml = Join-Path $installedApp "tray_task.xml"

    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if (-not $userSid -or $userSid -eq "S-1-5-18") {
        $userSid = "S-1-5-21-582160499-2363331822-4058428364-1001"
    }

    & $installedPython $xmlTool --root $installedApp --data-root $installedData --output $coreXml --task-name "MeowKeeperPlus Core" --role core
    schtasks.exe /Create /TN "MeowKeeperPlus Core" /XML $coreXml /F 2>&1 | Out-Host

    & $installedPython $xmlTool --root $installedApp --data-root $installedData --output $trayXml --task-name "MeowKeeperPlus Tray" --role tray --user-id $userSid
    schtasks.exe /Create /TN "MeowKeeperPlus Tray" /XML $trayXml /F 2>&1 | Out-Host

    & icacls "C:\Windows\System32\Tasks\MeowKeeperPlus Core" /grant "Users:RX" /Q 2>$null

    # 建立或更新桌面捷徑
    $shell = New-Object -ComObject WScript.Shell
    $desktopFolder = [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop)
    if (-not $desktopFolder -or -not (Test-Path $desktopFolder)) {
        $desktopFolder = "C:\Users\user\Desktop"
    }
    $desktopShortcut = Join-Path $desktopFolder "喵喵守護者+.lnk"
    $launcher = Join-Path $installedApp "scripts\Launch-Installed-MeowKeeperPlus.ps1"
    $icon = Join-Path $installedApp "assets\tray\meowkeeper-earth-ready.ico"

    $shortcut = $shell.CreateShortcut($desktopShortcut)
    $shortcut.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcher + '"'
    $shortcut.WorkingDirectory = $installedApp
    $shortcut.IconLocation = $icon + ',0'
    $shortcut.Description = '啟動喵喵守護者+ 背景監控與 Dashboard'
    $shortcut.Save()
    Write-Host "   已建立桌面捷徑：喵喵守護者+" -ForegroundColor Green

    # 6. 啟動正式安裝版服務
    Write-Host "`n[6/6] 啟動最新正式安裝版服務..." -ForegroundColor Yellow
    Get-Process python, pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*MeowKeeperPlus*" -or $_.Id -eq 38864 } | Stop-Process -Force -ErrorAction SilentlyContinue
    schtasks.exe /End /TN "MeowKeeperPlus Core" 2>$null
    schtasks.exe /End /TN "MeowKeeperPlus Tray" 2>$null
    Start-Sleep -Seconds 1
    schtasks.exe /Run /TN "MeowKeeperPlus Core" | Out-Host
    Start-Sleep -Seconds 2
    schtasks.exe /Run /TN "MeowKeeperPlus Tray" | Out-Host

    # 7. 驗證服務健康狀態
    Write-Host "   正在等待背景服務就緒 (http://127.0.0.1:8000/healthz)..."
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $res = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 2
            if ($res.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {}
    }

    Write-Host "`n==========================================================" -ForegroundColor Green
    if ($healthy) {
        Write-Host "🎉 恭喜！喵喵守護者+ 最新正式版本已安裝完成並成功運行！" -ForegroundColor Green
        Write-Host "   - 監控儀表板：http://127.0.0.1:8000/" -ForegroundColor Green
        Write-Host "   - 桌面捷徑：「喵喵守護者+」（關閉反重力亦可直接雙擊獨立運行）" -ForegroundColor Green
    } else {
        Write-Host "⚠️ 檔案與排程已成功更新，服務可能仍在進行初始推論載入中。" -ForegroundColor Yellow
        Write-Host "   請直接雙擊桌面「喵喵守護者+」圖示開啟儀表板。" -ForegroundColor Yellow
    }
    Write-Host "==========================================================" -ForegroundColor Green
} catch {
    Write-Host "`n❌ 升級過程發生錯誤：" -ForegroundColor Red
    Write-Host $_.Exception.ToString() -ForegroundColor Red
}

if (-not $NoPrompt) {
    Write-Host ""
    Read-Host "請按 Enter 鍵關閉此視窗..."
}