$ErrorActionPreference = 'Stop'
$tool = Join-Path $PSScriptRoot 'Manage-XiaomiUdpFirewall.ps1'
if (-not (Test-Path -LiteralPath $tool)) { throw '找不到小米攝影機 UDP 診斷工具。' }

Write-Host '喵喵守護者+｜小米攝影機連線與防火牆檢查'
Write-Host '請在與攝影機同一區網的 Windows 電腦執行；不需要輸入小米帳號或密碼。'
$cameraIp = (Read-Host '攝影機的區網 IPv4（可在路由器裝置清單查看）').Trim()
$streamName = (Read-Host 'go2rtc 串流名稱（不確定可留空；留空時不會測試影格）').Trim()
$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $tool,
    '-Mode', 'Diagnose', '-CameraIp', $cameraIp)
if ($streamName) { $arguments += @('-StreamName', $streamName) }
$diagnosis = & powershell.exe @arguments | ConvertFrom-Json
$diagnosis | ConvertTo-Json -Depth 4
if ($diagnosis.status -ne 'passed') {
    Write-Host '唯讀診斷尚未通過；沒有修改防火牆。請依錯誤階段排查。'
    exit 1
}
Write-Host '單張影格與持續取流狀態分開顯示；沒有取得影格不一定是防火牆。'
$answer = (Read-Host '是否同意建立僅限這個 go2rtc.exe、攝影機 IP、目前網卡與 UDP 的規則？輸入「同意」才繼續').Trim()
if ($answer -ne '同意') { Write-Host '未取得同意，沒有修改防火牆。'; exit 0 }
$arguments[[array]::IndexOf($arguments, 'Diagnose')] = 'Install'
$arguments += '-ConfirmRule'
if (@($diagnosis.blocking_rule_names).Count) {
    Write-Host '目前程式有適用網路的 UDP Block；新增 Allow 仍可能被封鎖。'
    $diagnosis.blocking_rule_names | ForEach-Object { Write-Host $_ }
    Write-Host '停用列出的 Block 會解除此程式原有封鎖；Allow 仍只限指定攝影機。將先備份。'
    $blockConsent = (Read-Host '另行同意停用上述指定 Block？輸入「同意停用」').Trim()
    if ($blockConsent -ne '同意停用') { Write-Host '沒有停用 Block，也沒有建立規則。'; exit 0 }
    $arguments += @('-ConfirmedBlockRules', ($diagnosis.blocking_rule_names -join ','))
}
$result = & powershell.exe @arguments | ConvertFrom-Json
$result | ConvertTo-Json -Depth 4
if ($result.status -ne 'passed') { exit 1 }
Write-Host '已完成規則核對；如需撤銷，使用 Manage-XiaomiUdpFirewall.ps1 -Mode Remove -CameraIp <攝影機IP>。'
