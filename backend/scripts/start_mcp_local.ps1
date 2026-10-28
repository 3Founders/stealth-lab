#Requires -Version 5.1
<#
.SYNOPSIS
  Start the StealthLab MCP server for local agent use (Cline, Cursor, Claude Code).

.DESCRIPTION
  Pins DEPLOYMENT_MODE=single_user for THIS PROCESS ONLY, then serves
  app.mcp_server.server:app on loopback with one worker.

  Why the pin is needed: backend/.env sets DEPLOYMENT_MODE=shared, and shared
  mode deliberately refuses the shared bearer token --
  server.py sets `allow_shared_token=(settings.deployment_mode != "shared")`,
  and _resolve_caller_identity returns None before ever comparing the secret.
  In shared mode the only identity is Supabase/OIDC browser sign-in, so a local
  MCP client that presents STEALTHLAB_MCP_TOKEN gets a bare 401 with nothing
  pointing at the cause. single_user restores the loopback shared-secret gate.

  Why it is set here and NOT in backend/.env: shared is the correct posture for
  the hosted service, and a persistent change to .env would silently apply to
  every other backend process (the FastAPI app on :8000, ingestion workers,
  tests). Pinning it on one process keeps the hosted posture intact.

.EXAMPLE
  .\scripts\start_mcp_local.ps1
.EXAMPLE
  .\scripts\start_mcp_local.ps1 -Force            # restart, replacing an existing server
.EXAMPLE
  .\scripts\start_mcp_local.ps1 -Port 8766        # run a second instance
#>
[CmdletBinding()]
param(
  [int]$Port = 8765,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'

$backend = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Push-Location $backend
try {
  $python = Join-Path $backend '.venv\Scripts\python.exe'
  if (-not (Test-Path $python)) { $python = 'python' }

  # Refuse to fight over the port: only ever stop something that looks like us.
  $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($busy) {
    $owner = $busy[0].OwningProcess
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue).CommandLine
    if (-not $cmdline -or $cmdline -notlike '*mcp_server.server*') {
      throw "Port $Port is held by PID $owner, which is not the StealthLab MCP server:`n  $cmdline`nRefusing to kill it. Use -Port to pick another."
    }
    if (-not $Force) {
      # Take it over rather than deadlocking the caller: a leftover server started
      # without DEPLOYMENT_MODE=single_user runs in .env's "shared" mode, which
      # refuses the shared bearer token, so every tool call 401s. Replacing it with
      # a correctly-configured one is strictly better than refusing to start.
      Write-Host "Port $Port is held by an existing MCP server (PID $owner) -- replacing it." -ForegroundColor Yellow
      Write-Host "(pass -Force to silence this notice; the old one is stopped either way)" -ForegroundColor DarkGray
    }
    else {
      Write-Host "Stopping existing MCP server (PID $owner)..." -ForegroundColor Yellow
    }
    Stop-Process -Id $owner -Force
    Start-Sleep -Seconds 3
  }

  $env:DEPLOYMENT_MODE = 'single_user'
  Write-Host "DEPLOYMENT_MODE=$env:DEPLOYMENT_MODE (this process only; backend/.env still says 'shared')" -ForegroundColor Cyan
  Write-Host "Serving http://127.0.0.1:$Port/mcp  (Ctrl+C to stop)" -ForegroundColor Cyan

  & $python -m uvicorn app.mcp_server.server:app --host 127.0.0.1 --port $Port --workers 1
}
finally {
  Pop-Location
}
