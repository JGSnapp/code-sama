#requires -Version 5.1
param(
  [switch]$Rebuild,
  [switch]$Down,
  [switch]$Logs
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Assert-Docker {
  docker info 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    $dd = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) {
      Write-Host "Starting Docker Desktop..." -ForegroundColor Cyan
      Start-Process $dd
      for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 3
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
      }
    }
    throw "Docker engine is not running. Start Docker Desktop and retry."
  }
}

Assert-Docker

if ($Down) {
  docker compose down
  Write-Host "Stopped." -ForegroundColor Yellow
  exit 0
}

if ($Logs) {
  docker compose logs -f --tail=100
  exit 0
}

if (-not (Test-Path ".\.env")) {
  Copy-Item ".\.env.example" ".\.env"
  Write-Host "Created .env from .env.example - edit API keys if needed." -ForegroundColor Yellow
}
if (-not (Test-Path ".\settings.json")) {
  '{}' | Set-Content ".\settings.json" -Encoding utf8
}
foreach ($d in @("workspace", "logs", "uploads", "characters")) {
  if (-not (Test-Path ".\$d")) { New-Item -ItemType Directory -Path ".\$d" | Out-Null }
}

Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

Write-Host "Building / starting Linux-isolated code-sama-os..." -ForegroundColor Cyan
if ($Rebuild) {
  docker compose up --build -d --force-recreate
} else {
  docker compose up --build -d
}

Write-Host "Waiting for health..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep -Seconds 2
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8765/" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $ready = $true; break }
  } catch { }
}
if (-not $ready) {
  Write-Host "Container logs:" -ForegroundColor Red
  docker compose logs --tail=80
  throw "Server did not become ready on :8765"
}

try {
  $st = Invoke-RestMethod -Uri "http://127.0.0.1:8765/loader/status" -TimeoutSec 5
} catch {
  $st = $null
}

Write-Host ""
Write-Host "code-sama-os (Docker / Linux) -> http://127.0.0.1:8765" -ForegroundColor Green
if ($st) {
  $color = if ($st.linux_available) { "Green" } else { "Yellow" }
  Write-Host ("linux_available = " + $st.linux_available) -ForegroundColor $color
  if ($st.capture_backend) { Write-Host ("capture_backend  = " + $st.capture_backend) }
}
Write-Host "Logs:  .\run-docker.ps1 -Logs"
Write-Host "Stop:  .\run-docker.ps1 -Down"
