#requires -Version 5.1
<#
.SYNOPSIS
  Start code-sama-os. Prefers Docker Linux isolation when available.

.PARAMETER Local
  Force host .venv mode (no Xvfb / Linux broker).
#>
param(
  [switch]$Local
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not $Local) {
  docker info 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Docker available — starting Linux-isolated mode (run-docker.ps1)." -ForegroundColor Cyan
    Write-Host "Tip: .\run.ps1 -Local  for host-only (no Xvfb broker)." -ForegroundColor DarkGray
    & "$PSScriptRoot\run-docker.ps1"
    exit $LASTEXITCODE
  }
  Write-Host "Docker not running — falling back to local venv (Linux broker unavailable)." -ForegroundColor Yellow
}

if (-not (Test-Path ".\.venv")) {
    Write-Host "Creating .venv..." -ForegroundColor Cyan
    python -m venv .venv
}

$python = ".\.venv\Scripts\python.exe"

function Test-PortFree($port) {
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        $l.Start()
        $l.Stop()
        return $true
    } catch { return $false }
}
$port = 0
foreach ($p in 8765, 18765, 28765, 38765, 48765) {
    if (Test-PortFree $p) { $port = $p; break }
}
if ($port -eq 0) { throw "No free port found in 8765/18765/28765/38765/48765" }

Write-Host "Installing requirements..." -ForegroundColor Cyan
& $python -m pip install -q --upgrade pip
& $python -m pip install -q -r requirements.txt

Write-Host "Installing Playwright browsers (chromium only)..." -ForegroundColor Cyan
& $python -m playwright install chromium | Out-Null

if (-not (Test-Path ".\.env")) {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "Created .env from .env.example -- edit PROXY_API_KEY if needed." -ForegroundColor Yellow
}

Write-Host "Starting code-sama-os (LOCAL / no Linux broker) on http://127.0.0.1:$port ..." -ForegroundColor Green
& $python -m uvicorn server.main:app --host 127.0.0.1 --port $port --reload
