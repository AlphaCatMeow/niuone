param(
    [ValidateSet("Install", "Status", "Restart", "Uninstall", "IsInstalled")]
    [string]$Action = "Install",
    [string]$Root = "",
    [string]$Python = "",
    [string]$LocalDataDir = "",
    [string]$EnvFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}
$Root = (Resolve-Path -LiteralPath $Root).Path
if (-not $LocalDataDir) {
    $LocalDataDir = Join-Path $Root ".local-data"
}
if (-not $EnvFile) {
    $EnvFile = Join-Path $LocalDataDir "dashboard.env"
}
if (-not $Python) {
    $Python = Join-Path $LocalDataDir ".venv\Scripts\python.exe"
}

$Runner = Join-Path $Root "scripts\run-windows-service.ps1"
$PowerShellExe = (Get-Process -Id $PID).Path
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Tasks = @(
    @{ TaskName = "NiuOne Dashboard"; ServiceName = "dashboard" },
    @{ TaskName = "NiuOne Cron Scheduler"; ServiceName = "cron-scheduler" },
    @{ TaskName = "NiuOne X Watchlist"; ServiceName = "x-watchlist" }
)

function Quote-TaskArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ($Value.Contains('"')) {
        throw "Task arguments cannot contain a double quote: $Value"
    }
    return '"' + $Value + '"'
}

function New-NiuOneTaskAction {
    param([Parameter(Mandatory = $true)][string]$ServiceName)
    $Arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-TaskArgument $Runner),
        "-Name", $ServiceName,
        "-Root", (Quote-TaskArgument $Root),
        "-Python", (Quote-TaskArgument $Python),
        "-LocalDataDir", (Quote-TaskArgument $LocalDataDir),
        "-EnvFile", (Quote-TaskArgument $EnvFile)
    ) -join " "
    return New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Arguments -WorkingDirectory $Root
}

function Show-NiuOneTasks {
    $Found = $false
    foreach ($Task in $Tasks) {
        $Scheduled = Get-ScheduledTask -TaskName $Task.TaskName -ErrorAction SilentlyContinue
        if ($null -ne $Scheduled) {
            $Found = $true
            $Info = Get-ScheduledTaskInfo -TaskName $Task.TaskName
            [PSCustomObject]@{
                TaskName = $Task.TaskName
                State = $Scheduled.State
                LastRunTime = $Info.LastRunTime
                LastTaskResult = $Info.LastTaskResult
                NextRunTime = $Info.NextRunTime
            }
        }
    }
    if (-not $Found) {
        Write-Host "No NiuOne scheduled tasks are installed."
    }
}

switch ($Action) {
    "IsInstalled" {
        if ($null -ne (Get-ScheduledTask -TaskName $Tasks[0].TaskName -ErrorAction SilentlyContinue)) {
            exit 0
        }
        exit 1
    }
    "Install" {
        if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
            throw "Python virtual environment is missing: $Python. Run run.bat once and retry."
        }
        if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
            throw "Windows service runner is missing: $Runner"
        }

        $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
        $Principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
        $Settings = New-ScheduledTaskSettingsSet `
            -RestartCount 999 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew `
            -StartWhenAvailable `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries

        foreach ($Task in $Tasks) {
            Stop-ScheduledTask -TaskName $Task.TaskName -ErrorAction SilentlyContinue
            $TaskAction = New-NiuOneTaskAction -ServiceName $Task.ServiceName
            Register-ScheduledTask `
                -TaskName $Task.TaskName `
                -Action $TaskAction `
                -Trigger $Trigger `
                -Settings $Settings `
                -Principal $Principal `
                -Description "NiuOne long-running process: $($Task.ServiceName)" `
                -Force | Out-Null
            Start-ScheduledTask -TaskName $Task.TaskName
        }

        Write-Host "NiuOne scheduled tasks installed and started for $Identity."
        Write-Host "  status:    powershell -File .\scripts\manage-long-running.ps1 -Action Status"
        Write-Host "  uninstall: powershell -File .\scripts\manage-long-running.ps1 -Action Uninstall"
        Show-NiuOneTasks | Format-Table -AutoSize
    }
    "Status" {
        Show-NiuOneTasks | Format-Table -AutoSize
    }
    "Restart" {
        foreach ($Task in $Tasks) {
            if ($null -ne (Get-ScheduledTask -TaskName $Task.TaskName -ErrorAction SilentlyContinue)) {
                Stop-ScheduledTask -TaskName $Task.TaskName -ErrorAction SilentlyContinue
                Start-ScheduledTask -TaskName $Task.TaskName
            }
        }
        Show-NiuOneTasks | Format-Table -AutoSize
    }
    "Uninstall" {
        foreach ($Task in $Tasks) {
            Stop-ScheduledTask -TaskName $Task.TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $Task.TaskName -Confirm:$false -ErrorAction SilentlyContinue
        }
        Write-Host "NiuOne scheduled tasks removed. Local data was kept at $LocalDataDir."
    }
}
