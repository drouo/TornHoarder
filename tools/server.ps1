<#
.SYNOPSIS
    Start/stop/restart/status/log for the TMA Streamlit server and its Tampermonkey update server.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\server.ps1 start
    powershell -ExecutionPolicy Bypass -File tools\server.ps1 stop
    powershell -ExecutionPolicy Bypass -File tools\server.ps1 status
    powershell -ExecutionPolicy Bypass -File tools\server.ps1 logs
    powershell -ExecutionPolicy Bypass -File tools\server.ps1 restart -Port 8502
    powershell -ExecutionPolicy Bypass -File tools\server.ps1 restart -NoWait
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status", "logs")]
    [string]$Command = "status",

    [int]$Port = 8501,

    [int]$UpdatePort = 9999,

    [switch]$NoWait
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$AppFile = Join-Path $RootDir "app\streamlit_app.py"
$UpdateFile = Join-Path $RootDir "tools\update_server.py"
$StateFile = Join-Path $PSScriptRoot ".tma_server.json"
$LogFile = Join-Path $PSScriptRoot ".tma_server.log"
$ErrFile = Join-Path $PSScriptRoot ".tma_server.err.log"

function Read-State {
    if (-not (Test-Path $StateFile)) { return @{} }
    try {
        return Get-Content $StateFile -Raw | ConvertFrom-Json -AsHashtable
    } catch {
        return @{}
    }
}

function Save-State {
    param(
        [int]$ProcId,
        [int]$Port,
        [int]$UpdatePid = 0,
        [int]$UpdatePort = 9999
    )
    @{
        pid = $ProcId
        port = $Port
        update_pid = $UpdatePid
        update_port = $UpdatePort
    } | ConvertTo-Json | Set-Content -Path $StateFile -Encoding ascii
}

function Test-Http {
    param([int]$Port)
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$Port" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return $resp.StatusCode -eq 200 -or $resp.StatusCode -eq 302
    } catch {
        return $false
    }
}

function Get-ListenerPid {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $conn) { return $null }
    return $conn.OwningProcess
}

function Get-PortOwner {
    param([int]$Port)
    $owner = Get-ListenerPid -Port $Port
    if (-not $owner) { return $null, $false }
    $cmd = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction SilentlyContinue
    $isOurs = $null -ne $cmd -and $cmd.CommandLine -match "streamlit.*run.*streamlit_app\.py"
    return $owner, [bool]$isOurs
}

function Get-UpdateServerOwner {
    param([int]$Port)
    $owner = Get-ListenerPid -Port $Port
    if (-not $owner) { return $null, $false }
    $cmd = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction SilentlyContinue
    $isOurs = $null -ne $cmd -and $cmd.CommandLine -match "update_server"
    return $owner, [bool]$isOurs
}

function Start-UpdateServer {
    if (-not (Test-Path $UpdateFile)) { return 0 }
    $owner, $isOurs = Get-UpdateServerOwner -Port $UpdatePort
    if ($owner -and $isOurs) { return $owner }
    if ($owner) { return 0 }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { return 0 }

    $proc = Start-Process -FilePath $python.Source `
        -ArgumentList $UpdateFile, "--port=$UpdatePort" `
        -WorkingDirectory $RootDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$PSScriptRoot\.update_server.log" `
        -RedirectStandardError "$PSScriptRoot\.update_server.err.log"
    Start-Sleep -Milliseconds 500
    $owner2, $isOurs2 = Get-UpdateServerOwner -Port $UpdatePort
    return $owner2
}

function Stop-UpdateServer {
    $state = Read-State
    $upid = $state["update_pid"]
    if ($upid -and $upid -gt 0) {
        $cmd = Get-CimInstance Win32_Process -Filter "ProcessId = $upid" -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.CommandLine -match "update_server") {
            taskkill /PID $upid /F | Out-Null
        }
    }
}

function Start-TmaServer {
    if (-not (Test-Path $AppFile)) {
        Write-Error "App not found: $AppFile"
        exit 1
    }

    $owner, $isOurs = Get-PortOwner -Port $Port
    if ($owner -and $isOurs) {
        $up = Start-UpdateServer
        Save-State -ProcId $owner -Port $Port -UpdatePid $up -UpdatePort $UpdatePort
        Write-Host "Already running on http://localhost:$Port (pid $owner). Update server on port $UpdatePort."
        return
    }
    if ($owner) {
        Write-Host "Port $Port is held by external pid $owner; not starting over it."
        exit 1
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Error "python not found on PATH."
        exit 1
    }

    $args = @(
        "-m", "streamlit", "run", $AppFile,
        "--server.port=$Port"
    )

    $proc = Start-Process -FilePath $python.Source -ArgumentList $args `
        -WorkingDirectory $RootDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile

    $up = Start-UpdateServer

    if ($NoWait) {
        Save-State -ProcId $proc.Id -Port $Port -UpdatePid $up -UpdatePort $UpdatePort
        Write-Host "Started on http://localhost:$Port (pid $($proc.Id)) [no-wait]. Update server on port $UpdatePort."
        return
    }

    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited -and -not (Test-Http -Port $Port)) {
            Write-Error "Server exited early (code $($proc.ExitCode)). Check $ErrFile"
            exit 1
        }
        if (Test-Http -Port $Port) {
            Start-Sleep -Seconds 1
            $owner2, $isOurs2 = Get-PortOwner -Port $Port
            if (-not $owner2 -or -not $isOurs2) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Write-Error "Started but could not claim port $Port (owner $owner2); check $ErrFile"
                exit 1
            }
            Save-State -ProcId $owner2 -Port $Port -UpdatePid $up -UpdatePort $UpdatePort
            Write-Host "Started on http://localhost:$Port (pid $owner2). Update server on port $UpdatePort."
            return
        }
        Start-Sleep -Milliseconds 300
    }
    Write-Error "Server did not answer on port $Port within 60s. Check $ErrFile"
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

function Stop-TmaServer {
    Stop-UpdateServer

    $owner, $isOurs = Get-PortOwner -Port $Port
    if ($owner -and $isOurs) {
        taskkill /PID $owner /T /F | Out-Null
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and (Get-ListenerPid -Port $Port)) {
            Start-Sleep -Milliseconds 200
        }
        Write-Host "Stopped pid $owner."
    } elseif ($owner) {
        Write-Host "Port $Port belongs to external pid $owner; refusing to kill it."
    } else {
        Write-Host "No TMA server on port $Port."
    }
    Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
}

function Show-Status {
    $owner, $isOurs = Get-PortOwner -Port $Port
    if ($owner -and $isOurs) {
        Write-Host "streamlit - http://localhost:$Port - pid $owner"
    } elseif ($owner) {
        Write-Host "streamlit - port $Port held by external pid $owner"
    } else {
        Write-Host "streamlit - not running"
    }

    $uowner, $uisOurs = Get-UpdateServerOwner -Port $UpdatePort
    if ($uowner -and $uisOurs) {
        Write-Host "update    - http://localhost:$UpdatePort - pid $uowner"
    } elseif ($uowner) {
        Write-Host "update    - port $UpdatePort held by external pid $uowner"
    } else {
        Write-Host "update    - not running"
    }
}

function Show-Logs {
    if (Test-Path $LogFile) {
        Write-Host "--- streamlit stdout ($LogFile) ---"
        Get-Content $LogFile -Tail 50
    }
    if (Test-Path $ErrFile) {
        Write-Host "--- streamlit stderr ($ErrFile) ---"
        Get-Content $ErrFile -Tail 50
    }
    $ulog = Join-Path $PSScriptRoot ".update_server.log"
    $uerr = Join-Path $PSScriptRoot ".update_server.err.log"
    if (Test-Path $ulog) {
        Write-Host "--- update server stdout ($ulog) ---"
        Get-Content $ulog -Tail 20
    }
    if (Test-Path $uerr) {
        Write-Host "--- update server stderr ($uerr) ---"
        Get-Content $uerr -Tail 20
    }
    if (-not (Test-Path $LogFile) -and -not (Test-Path $ErrFile)) {
        Write-Host "No logs yet."
    }
}

switch ($Command) {
    "start"   { Start-TmaServer }
    "stop"    { Stop-TmaServer }
    "restart" { Stop-TmaServer; Start-TmaServer }
    "status"  { Show-Status }
    "logs"    { Show-Logs }
}
