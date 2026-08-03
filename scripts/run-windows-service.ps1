param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("dashboard", "cron-scheduler", "x-watchlist")]
    [string]$Name,
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$LocalDataDir,
    [Parameter(Mandatory = $true)][string]$EnvFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$env:NIUONE_LOCAL_DATA_DIR = $LocalDataDir
$env:DASHBOARD_ENV_FILE = $EnvFile
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUNBUFFERED = "1"
Set-Location -LiteralPath $Root

$LogDir = Join-Path $LocalDataDir "runtime\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogPath = Join-Path $LogDir "windows-service-$Name.log"
Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "$(Get-Date -Format o) starting $Name"

$ExitCode = 1
try {
    switch ($Name) {
        "dashboard" {
            $Launcher = Join-Path $Root "run.bat"
            $env:NIUONE_MANAGED_SERVICE_CHILD = "1"
            & $Launcher --no-browser --skip-install *>> $LogPath
        }
        "cron-scheduler" {
            $Script = Join-Path $Root "app\entrypoints\niuone_cron_scheduler.py"
            & $Python $Script *>> $LogPath
        }
        "x-watchlist" {
            $Script = Join-Path $Root "app\entrypoints\x_watchlist_daemon.py"
            & $Python $Script *>> $LogPath
        }
    }
    $ExitCode = $LASTEXITCODE
}
catch {
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "$(Get-Date -Format o) $($_.Exception.ToString())"
    $ExitCode = 1
}

Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "$(Get-Date -Format o) stopped $Name exit=$ExitCode"
exit $ExitCode
