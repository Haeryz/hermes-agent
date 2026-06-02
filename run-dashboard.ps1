param(
    [int]$Port = 9119,
    [string]$HostAddress = "127.0.0.1",
    [switch]$NoOpen,
    [switch]$NoSkipBuild,
    [switch]$NoChat
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Hermes = Join-Path $Root ".venv\Scripts\hermes.exe"
$WebDist = Join-Path $Root "hermes_cli\web_dist\index.html"
$LogDir = Join-Path $Root ".dashboard"
$OutLog = Join-Path $LogDir "dashboard.out.log"
$ErrLog = Join-Path $LogDir "dashboard.err.log"

if (-not (Test-Path $Hermes)) {
    throw "Hermes executable not found: $Hermes"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Get-PortListeners {
    param([int]$ListenPort)

    Get-NetTCPConnection -LocalPort $ListenPort -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq "Listen" }
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($proc) { return [string]$proc.CommandLine }
    return ""
}

function Test-HermesDashboardCommandLine {
    param([string]$CommandLine)

    return (
        ($CommandLine -match "hermes(\.exe)?" -or $CommandLine -match "hermes_cli\.main") -and
        $CommandLine -match "\bdashboard\b"
    )
}

Write-Host "Stopping existing Hermes dashboard..."
& $Hermes dashboard --stop | Out-Host

Start-Sleep -Milliseconds 500

$conn = Get-PortListeners -ListenPort $Port

foreach ($c in $conn) {
    $cmd = Get-ProcessCommandLine -ProcessId $c.OwningProcess
    if (Test-HermesDashboardCommandLine -CommandLine $cmd) {
        Write-Host "Killing stale dashboard port owner PID $($c.OwningProcess)..."
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Milliseconds 500
$remaining = Get-PortListeners -ListenPort $Port
if ($remaining) {
    foreach ($c in $remaining) {
        $cmd = Get-ProcessCommandLine -ProcessId $c.OwningProcess
        Write-Host "Port $Port is still owned by PID $($c.OwningProcess): $cmd"
    }
    throw "Port $Port is still in use by a non-Hermes dashboard process."
}

$argsList = @("dashboard", "--host", $HostAddress, "--port", "$Port", "--no-open")
if (-not $NoChat) {
    $argsList += "--tui"
}
if (-not $NoSkipBuild -and (Test-Path $WebDist)) {
    $argsList += "--skip-build"
}

Write-Host "Starting Hermes dashboard on http://${HostAddress}:$Port ..."
$process = Start-Process `
    -FilePath $Hermes `
    -ArgumentList $argsList `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

$deadline = (Get-Date).AddSeconds(30)
$statusOk = $false
do {
    Start-Sleep -Milliseconds 300
    if ($process.HasExited) {
        Write-Host "Dashboard exited with code $($process.ExitCode)."
        if (Test-Path $OutLog) { Get-Content $OutLog -Tail 80 | Out-Host }
        if (Test-Path $ErrLog) { Get-Content $ErrLog -Tail 80 | Out-Host }
        exit 1
    }

    try {
        $response = Invoke-WebRequest -UseBasicParsing "http://${HostAddress}:$Port/api/status" -TimeoutSec 2
        $statusOk = $response.StatusCode -eq 200
    } catch {
        $statusOk = $false
    }
} until ($statusOk -or (Get-Date) -gt $deadline)

if (-not $statusOk) {
    Write-Host "Dashboard process started as PID $($process.Id), but /api/status did not return HTTP 200 within 30 seconds."
    Write-Host "Logs:"
    Write-Host "  $OutLog"
    Write-Host "  $ErrLog"
    exit 1
}

$url = "http://${HostAddress}:$Port"
Write-Host "Dashboard ready: $url"
Write-Host "PID: $($process.Id)"
Write-Host "Logs:"
Write-Host "  $OutLog"
Write-Host "  $ErrLog"

if (-not $NoOpen) {
    Start-Process $url
}
