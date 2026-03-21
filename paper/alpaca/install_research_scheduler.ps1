param(
    [string]$TaskName = "WQA_Alpaca_Research_2300",
    [string]$CondaEnv = "alpaca-paper",
    [string]$RunAt = "23:00"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
$runnerPath = (Resolve-Path (Join-Path $scriptDir "search_runner.py")).Path

$command = @"
powershell -NoProfile -Command "& {
    Set-Location '$repoRoot'
    New-Item -ItemType Directory -Force 'paper/alpaca/logs' | Out-Null
    `$d = Get-Date -Format 'yyyy-MM-dd'
    `$log = \"paper/alpaca/logs/research_`$d.log\"
    conda run -n $CondaEnv python '$runnerPath' --resume *> `$log
}"
"@

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c $command"

$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $RunAt

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Nightly Alpaca alpha search pipeline with resume + shadow artifact output." `
    -Force

Write-Host "Task installed: $TaskName"
Write-Host "Run command:"
Write-Host "  conda run -n $CondaEnv python paper/alpaca/search_runner.py --resume"
Write-Host "Test immediate run:"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
