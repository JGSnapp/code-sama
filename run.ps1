#requires -Version 5.1
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".\.venv")) {
    Write-Host "Creating .venv..." -ForegroundColor Cyan
    python -m venv .venv
}

$python = ".\.venv\Scripts\python.exe"

# Pick a working port. Windows reserves some low ports (HTTP.SYS), so we try
# 8765 first and fall back to higher ports if the bind would be forbidden.
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

Write-Host "Starting code-sama-os on http://127.0.0.1:$port ..." -ForegroundColor Green
& $python -m uvicorn server.main:app --host 127.0.0.1 --port $port --reload
