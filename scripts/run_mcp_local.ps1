# Run the kell MCP server locally, for when the hosted one is down.
#
# It loads backend/.env the way pydantic-settings does (inline `#` comments
# are comments, not part of the value) and removes `channel_binding`, which
# asyncpg does not accept. It does NOT run migrations: the database is the
# one in DATABASE_URL, and the server only serves.
#
# Usage:
#   powershell -File scripts\run_mcp_local.ps1
#   powershell -File scripts\run_mcp_local.ps1 -Port 8765
#
# Then point the desktop at it (back up ~/.stealthlab/config.json first):
#   "url": "http://127.0.0.1:8765/mcp"
# and press Retry in the app, or restart kell.

[CmdletBinding()]
param([int]$Port = 8765, [string]$ConnectionsFile = "")

$ErrorActionPreference = "Stop"

# Idempotent for a logon task: a second copy must not fight the first for the port.
$already = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($already) {
  Write-Host "kell MCP server is already listening on $Port"
  exit 0
}

$backend = Join-Path (Split-Path -Parent $PSScriptRoot) "backend"
Set-Location $backend

if (-not (Test-Path ".env")) { throw "backend/.env is missing" }

Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    $k = $matches[1]
    $v = $matches[2].Trim()
    if ($v.Length -ge 2 -and (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'")))) {
      $v = $v.Substring(1, $v.Length - 2)
    } else {
      $v = ($v -replace '\s+#.*$', '').Trim()          # an inline comment is not part of the value
    }
    if ($k -in @('DATABASE_URL', 'DATABASE_URL_DIRECT', 'CONTROL_DATABASE_URL')) {
      $v = ($v -replace 'channel_binding=[^&]*&?', '') -replace '\?&', '?'
      $v = $v.TrimEnd('?', '&')
    }
    [Environment]::SetEnvironmentVariable($k, $v, 'Process')
  }
}

if (-not $env:DATABASE_URL) { throw "DATABASE_URL is not set in backend/.env" }

# The provider connections file (deploy/provider_connections.json) is what makes
# /v1/models non-empty. ALWAYS point at this checkout's file: a stale
# STEALTH_PROVIDER_CONNECTIONS_FILE (for example the hosted /repo/backend/... path)
# otherwise loads nothing and the app says "No models are available to this account yet."
# Pass -ConnectionsFile only when deliberately testing another file.
$connections = if ($ConnectionsFile) { $ConnectionsFile } else { Join-Path $backend "deploy\provider_connections.json" }
$env:STEALTH_PROVIDER_CONNECTIONS_FILE = $connections
if (-not (Test-Path $connections)) {
  throw "provider connections file not found: $connections"
}

Write-Host "kell MCP server on http://127.0.0.1:$Port (db: $(([uri]$env:DATABASE_URL).Host))"
Write-Host "provider connections: $connections"
python -m uvicorn app.mcp_server.server:app --host 127.0.0.1 --port $Port --workers 1 --log-level info
