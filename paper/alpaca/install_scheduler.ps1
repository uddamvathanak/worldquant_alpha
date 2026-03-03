param(
    [string]$TaskName = "WQA_Alpaca_Rebalance_0935ET",
    [string]$CondaEnv = "alpaca-paper",
    [string]$RunAt = "09:35",
    [string]$EtOpenTime = "09:35",
    [object]$TrackEtMarketOpen = $true,
    [int]$EtWindowMinutes = 20
)

$ErrorActionPreference = "Stop"

function Resolve-BoolParam {
    param(
        [Parameter(Mandatory = $true)]
        $Value,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if ($Value -is [bool]) {
        return [bool]$Value
    }

    $text = "$Value".Trim().ToLowerInvariant()
    switch -Regex ($text) {
        '^(1|true|\$true|yes|y|on)$' { return $true }
        '^(0|false|\$false|no|n|off)$' { return $false }
        default {
            throw "Invalid '$Name' value '$Value'. Use true/false or 1/0."
        }
    }
}

function Get-LocalTriggerTimesFromEt {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EtTime
    )

    $etZoneId = "Eastern Standard Time"
    try {
        $etZone = [System.TimeZoneInfo]::FindSystemTimeZoneById($etZoneId)
    } catch {
        Write-Warning "Could not resolve ET zone id '$etZoneId'. Falling back to local RunAt=$RunAt."
        return @($RunAt)
    }

    $localZone = [System.TimeZoneInfo]::Local
    $year = (Get-Date).Year
    $samples = @(
        [datetime]::ParseExact("$year-01-15 $EtTime", "yyyy-MM-dd HH:mm", $null),
        [datetime]::ParseExact("$year-07-15 $EtTime", "yyyy-MM-dd HH:mm", $null)
    )

    $times = @()
    foreach ($sample in $samples) {
        $etWallClock = [datetime]::SpecifyKind($sample, [System.DateTimeKind]::Unspecified)
        $localWallClock = [System.TimeZoneInfo]::ConvertTime($etWallClock, $etZone, $localZone)
        $times += $localWallClock.ToString("HH:mm")
    }
    return @($times | Sort-Object -Unique)
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
$runnerPath = (Resolve-Path (Join-Path $scriptDir "daily_pipeline.py")).Path
$TrackEtMarketOpen = Resolve-BoolParam -Value $TrackEtMarketOpen -Name "TrackEtMarketOpen"

$runnerArgs = ""
if ($TrackEtMarketOpen) {
    $runnerArgs = "--enforce-et-window --et-target-time $EtOpenTime --et-window-minutes $EtWindowMinutes"
}

$command = "cd /d ""$repoRoot"" && conda run -n $CondaEnv python ""$runnerPath"" $runnerArgs"
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c $command"

$triggerTimes = if ($TrackEtMarketOpen) {
    Get-LocalTriggerTimesFromEt -EtTime $EtOpenTime
} else {
    @($RunAt)
}

$triggers = @()
foreach ($time in $triggerTimes) {
    $triggers += New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
        -At $time
}

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
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Alpaca daily signal+rebalance pipeline at 09:35 ET for WQA paper trial." `
    -Force

Write-Host "Task installed: $TaskName"
if ($TrackEtMarketOpen) {
    Write-Host "ET market-time tracking enabled (ET $EtOpenTime)."
    Write-Host "Local trigger times: $($triggerTimes -join ', ')"
    Write-Host "ET execution window: +/- $EtWindowMinutes minutes"
} else {
    Write-Host "Local fixed trigger time: $($triggerTimes -join ', ')"
}
Write-Host "Run command:"
if ($TrackEtMarketOpen) {
    Write-Host "  conda run -n $CondaEnv python paper/alpaca/daily_pipeline.py $runnerArgs"
} else {
    Write-Host "  conda run -n $CondaEnv python paper/alpaca/daily_pipeline.py"
}
Write-Host "Test immediate run:"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
