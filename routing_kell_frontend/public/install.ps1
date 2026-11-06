# kell MCP installer for Windows (PowerShell 5.1+ / 7+).
#
#   irm https://<site>/install.ps1 | iex
#
# `irm | iex` cannot pass arguments; set them as environment variables first:
#   $env:STEALTHLAB_MCP_URL = "https://<host>/mcp"     # override the endpoint
#   $env:STEALTHLAB_CLIENTS = "claude-code,cursor"     # only these clients
#   $env:STEALTHLAB_TOKEN   = "<token>"                # optional; reads are anonymous
#   $env:STEALTHLAB_MCP_PACKAGE = "kell-mcp@0.2.0" # optional; pin a version
#
# Installs nothing kell-side on this machine: it registers the HOSTED
# kell MCP endpoint with the coding agents it finds (Claude Code,
# Cursor, VS Code, Windsurf, Codex, Claude Desktop) and exits. With Node.js
# 18+ it runs `npx kell-mcp install`; without Node it still registers
# Claude Code directly via `claude mcp add --transport http`.

& {
    $ErrorActionPreference = "Stop"

    # Hosted endpoint. Empty until the production URL is fixed.
    $DefaultUrl = "https://mcp.kell.org.in/mcp"
    $Pkg = if ($env:STEALTHLAB_MCP_PACKAGE) { $env:STEALTHLAB_MCP_PACKAGE } else { "kell-mcp@latest" }  # override to pin a version

    $Url = if ($env:STEALTHLAB_MCP_URL) { $env:STEALTHLAB_MCP_URL } else { $DefaultUrl }
    if (-not $Url) {
        Write-Host "error: no MCP URL. Run:  `$env:STEALTHLAB_MCP_URL='https://<host>/mcp'; irm <site>/install.ps1 | iex" -ForegroundColor Red
        return
    }

    $cliArgs = @("install", "--url", $Url)
    if ($env:STEALTHLAB_CLIENTS) { $cliArgs += @("--client", $env:STEALTHLAB_CLIENTS) }
    if ($env:STEALTHLAB_TOKEN)   { $cliArgs += @("--token", $env:STEALTHLAB_TOKEN) }

    $nodeOk = $false
    if ((Get-Command node -ErrorAction SilentlyContinue) -and (Get-Command npx -ErrorAction SilentlyContinue)) {
        & node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>18||(a===18&&b>=17)?0:1)'
        $nodeOk = ($LASTEXITCODE -eq 0)
    }

    if ($nodeOk) {
        Write-Host "==> Registering kell MCP ($Url) with your agents" -ForegroundColor Cyan
        & npx -y "--package=$Pkg" kell @cliArgs
        return
    }

    Write-Host "Node.js 18.17+ not found -- registering what can be done without it." -ForegroundColor Yellow
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        & claude mcp remove --scope user stealthlab 2>$null | Out-Null
        if ($env:STEALTHLAB_TOKEN) {
            & claude mcp add --scope user --transport http stealthlab $Url --header "Authorization: Bearer $($env:STEALTHLAB_TOKEN)"
        } else {
            & claude mcp add --scope user --transport http stealthlab $Url
        }
        Write-Host "  ok    Claude Code" -ForegroundColor Green
    } else {
        Write-Host "  Claude Code not found."
    }
    Write-Host ""
    Write-Host "For other agents, add this remote MCP server in their settings:"
    Write-Host "  { `"stealthlab`": { `"url`": `"$Url`" } }"
    Write-Host "Or install Node.js (https://nodejs.org) and re-run this command to configure them automatically."
}
