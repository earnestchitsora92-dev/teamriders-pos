<#
Starts Team Riders POS and publishes it at https://www.teamriders.bid
through the named Cloudflare tunnel "teamriders".

Usage:
    .\start-online.ps1
    .\start-online.ps1 -TunnelName teamriders -PublicUrl https://www.teamriders.bid

Safe to run more than once: an existing app server or tunnel is reused.
Ctrl+C stops the tunnel (and the app server, if this script started it).
#>
param(
    [string]$TunnelName = "teamriders",
    [string]$PublicUrl = "https://www.teamriders.bid",
    [int]$Port = 8077
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$cloudflared = Join-Path $env:LOCALAPPDATA "cloudflared\cloudflared.exe"
$config = Join-Path $env:USERPROFILE ".cloudflared\config.yml"

if (-not (Test-Path $cloudflared)) {
    throw "cloudflared.exe not found at $cloudflared. Reinstall it or edit this script."
}
if (-not (Test-Path $config)) {
    throw "Cloudflare tunnel config not found at $config."
}

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$tunnelLog = Join-Path $logDir "cloudflared.log"

function Test-App {
    try {
        $response = Invoke-WebRequest "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

$tunnelProcess = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*tunnel*run*$TunnelName*" }
if ($tunnelProcess) {
    Write-Host "Tunnel already running: $PublicUrl" -ForegroundColor Green
    exit 0
}

$server = $null
if (-not (Test-App)) {
    Write-Host "Starting Team Riders POS on port $Port ..."
    $server = Start-Process -FilePath "python" `
        -ArgumentList "run.py", "--port", "$Port" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "server.out.log") `
        -RedirectStandardError (Join-Path $logDir "server.err.log")

    $started = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-App) { $started = $true; break }
    }
    if (-not $started) {
        throw "The app did not start. Check logs\server.err.log."
    }
}

Write-Host ""
Write-Host " Local:  http://127.0.0.1:$Port"
Write-Host " Public: $PublicUrl" -ForegroundColor Green
Write-Host " Logs:   $logDir" -ForegroundColor DarkGray
Write-Host " Keep this window open. Press Ctrl+C to stop." -ForegroundColor Yellow
Write-Host ""

try {
    while ($true) {
        & $cloudflared tunnel --config $config --logfile $tunnelLog --loglevel info run $TunnelName
        if ($LASTEXITCODE -eq 0) { break }
        Write-Host "Tunnel stopped (exit $LASTEXITCODE). Retrying in 30 seconds..." -ForegroundColor Yellow
        Start-Sleep -Seconds 30
    }
} finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}
