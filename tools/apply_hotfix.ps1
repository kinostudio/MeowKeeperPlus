[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$host.UI.RawUI.WindowTitle = "喵喵守護者+ HOTFIX 套用工具"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  [喵喵守護者+] 套用 HOTFIX (影片清理 + 多API獨立配額 + 一鍵熱修復)" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$gitSrc = "C:\Users\user\Desktop\MeowKeeperPlus-Git\src\meowkeeper"
$appSrc = "C:\ProgramData\MeowKeeperPlus\App\src\meowkeeper"
$gitTools = "C:\Users\user\Desktop\MeowKeeperPlus-Git\tools"
$appTools = "C:\ProgramData\MeowKeeperPlus\App\tools"

Write-Host "[1/4] 同步更新代碼至已安裝目錄..." -ForegroundColor Green
$rc1 = Start-Process -FilePath "robocopy" -ArgumentList "`"$gitSrc`" `"$appSrc`" /E /XO /R:1 /W:1 /NDL /NFL /NJH /NJS" -Wait -PassThru -NoNewWindow
$rc2 = Start-Process -FilePath "robocopy" -ArgumentList "`"$gitTools`" `"$appTools`" /E /XO /R:1 /W:1 /NDL /NFL /NJH /NJS" -Wait -PassThru -NoNewWindow
if ($rc1.ExitCode -lt 8 -and $rc2.ExitCode -lt 8) {
    Write-Host "      代碼同步完成。" -ForegroundColor Gray
} else {
    Write-Host "      [提示] robocopy 返回碼: $($rc1.ExitCode), $($rc2.ExitCode)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[2/4] 停止 MeowKeeperPlus Core 服務..." -ForegroundColor Green
$null = Start-Process -FilePath "schtasks" -ArgumentList "/End", "/TN", "`"MeowKeeperPlus Core`"" -Wait -NoNewWindow
Start-Sleep -Seconds 3

Write-Host "[3/4] 啟動 MeowKeeperPlus Core 服務..." -ForegroundColor Green
$null = Start-Process -FilePath "schtasks" -ArgumentList "/Run", "/TN", "`"MeowKeeperPlus Core`"" -Wait -NoNewWindow
Start-Sleep -Seconds 4

Write-Host ""
Write-Host "[4/4] 檢查服務健康狀態..." -ForegroundColor Green
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
Write-Host "  HOTFIX 已套用完成！" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "請按任意鍵或 Enter 關閉本視窗..."
$null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")