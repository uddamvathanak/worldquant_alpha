param(
    [string]$TaskName = "WQA_Alpaca_Rebalance_0935ET",
    [string]$CondaEnv = "alpaca-paper",
    [string]$RunAt = "09:35"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
$runnerPath = (Resolve-Path (Join-Path $scriptDir "rebalance_runner.py")).Path

$command = "cd `"$repoRoot`"; conda run -n $CondaEnv python `"$runnerPath`""
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$command`""

$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $RunAt

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel LeastPrivilege

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Alpaca daily rebalance at 09:35 ET for WQA paper trial." `
    -Force

Write-Host "Task installed: $TaskName"
Write-Host "Run command:"
Write-Host "  conda run -n $CondaEnv python paper/alpaca/rebalance_runner.py"
Write-Host "Test immediate run:"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"

