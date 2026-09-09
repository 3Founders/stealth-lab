$ErrorActionPreference = "Stop"

$backendRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $backendRoot ".env"

if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Missing backend/.env"
}

$tokenLine = Get-Content -LiteralPath $envPath |
    Where-Object { $_ -match '^STEALTHLAB_MCP_TOKEN=' } |
    Select-Object -First 1

if (-not $tokenLine) {
    throw "STEALTHLAB_MCP_TOKEN is not set in backend/.env"
}

$stealthlabToken = $tokenLine.Substring($tokenLine.IndexOf('=') + 1).Trim()
if (-not $stealthlabToken) {
    throw "STEALTHLAB_MCP_TOKEN is empty in backend/.env"
}

Push-Location $backendRoot
try {
    & npx @modelcontextprotocol/inspector `
        --web `
        --transport http `
        --server-url http://127.0.0.1:8765/mcp `
        --header "Authorization: Bearer $stealthlabToken"

    if ($LASTEXITCODE -ne 0) {
        throw "MCP Inspector exited with code $LASTEXITCODE"
    }
}
finally {
    Remove-Variable stealthlabToken -ErrorAction SilentlyContinue
    Pop-Location
}
