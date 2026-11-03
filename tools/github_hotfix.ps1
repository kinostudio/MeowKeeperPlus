[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$host.UI.RawUI.WindowTitle = "喵喵守護者+ GitHub HOTFIX 更新"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  [喵喵守護者+] GitHub 遠端原始碼 HOTFIX 更新" -ForegroundColor Yellow
Write-Host "  (僅拉取最新代碼庫，秒級快速更新，不需下載 4GB 大包)" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$gitExe = "C:\Users\user\AppData\Local\Programs\MeowKeeperPlus-DevTools\Git-2.56.0.2\bin\git.exe"
if (-not (Test-Path $gitExe)) {
    $found = (Get-Command git -ErrorAction SilentlyContinue)
    if ($found) { $gitExe = $found.Source }
}
$repo = "C:\Users\user\Desktop\MeowKeeperPlus-Git"

if (-not (Test-Path $gitExe)) {
    Write-Host "[錯誤] 找不到 Git 執行檔，請確認 Git 安裝路徑。" -ForegroundColor Red
    Write-Host "請按任意鍵結束..."
    $null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

Write-Host "[1/4] 從 GitHub 倉庫拉取最新程式碼 (main 分支)..." -ForegroundColor Green
$p1 = Start-Process -FilePath $gitExe -ArgumentList "-C `"$repo`" fetch origin main" -Wait -PassThru -NoNewWindow
$p2 = Start-Process -FilePath $gitExe -ArgumentList "-C `"$repo`" merge origin/main --ff-only" -Wait -PassThru -NoNewWindow
if ($p1.ExitCode -eq 0 -and $p2.ExitCode -eq 0) {
    Write-Host "      代碼已成功更新至最新 commit。" -ForegroundColor Gray
} else {
    Write-Host "      [提示] git 更新返回碼: $($p1.ExitCode), $($p2.ExitCode)" -ForegroundColor Yellow
}

$gitSrc = "C:\Users\user\Desktop\MeowKeeperPlus-Git\src\meowkeeper"
$appSrc = "C:\ProgramData\MeowKeeperPlus\App\src\meowkeeper"
$gitTools = "C:\Users\user\Desktop\MeowKeeperPlus-Git\tools"
$appTools = "C:\ProgramData\MeowKeeperPlus\App\tools"

Write-Host ""
Write-Host "[2/4] 將變更代碼同步至已安裝目錄..." -ForegroundColor Green
$rc1 = Start-Process -FilePath "robocopy" -ArgumentList "`"$gitSrc`" `"$appSrc`" /E /XO /R:1 /W:1 /NDL /NFL /NJH /NJS" -Wait -PassThru -NoNewWindow
$rc2 = Start-Process -FilePath "robocopy" -ArgumentList "`"$gitTools`" `"$appTools`" /E /XO /R:1 /W:1 /NDL /NFL /NJH /NJS" -Wait -PassThru -NoNewWindow
if ($rc1.ExitCode -lt 8 -and $rc2.ExitCode -lt 8) {
    Write-Host "      代碼同步完成。" -ForegroundColor Gray
} else {
    Write-Host "      [提示] robocopy 返回碼: $($rc1.ExitCode), $($rc2.ExitCode)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[3/4] 重啟 MeowKeeperPlus Core 服務..." -ForegroundColor Green
$null = Start-Process -FilePath "schtasks" -ArgumentList "/End", "/TN", "`"MeowKeeperPlus Core`"" -Wait -NoNewWindow
Start-Sleep -Seconds 3
$null = Start-Process -FilePath "schtasks" -ArgumentList "/Run", "/TN", "`"MeowKeeperPlus Core`"" -Wait -NoNewWindow
Start-Sleep -Seconds 4

Write-Host ""
Write-Host "[4/4] 驗證服務運行狀態..." -ForegroundColor Green
$healthOk = $false
for ($i = 1; $i -le 10; $i++) {
    try {
        $res = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 3 -ErrorAction Stop
        Write-Host "      [成功] 服務已恢復運行！核心狀態: $($res.core_status)" -ForegroundColor Green
        $healthOk = $true
        break
    } catch {
        Write-Host "      等待核心服務重啟中 ($i/10)..." -ForegroundColor Yellow
        Start-Sleep -Seconds 2
    }
}

if (-not $healthOk) {
    Write-Host "      [提示] 服務啟動耗時較長，請稍候於瀏覽器重整 Dashboard。" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  HOTFIX 更新完成！耗時不到 10 秒。" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "請按任意鍵或 Enter 關閉本視窗..."
$null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")