#requires -Version 5.1
<#
.SYNOPSIS
    One-command bring-up for a personal StealthLab instance.

.DESCRIPTION
    Automates every setup step that ISN'T a real human decision:
    Docker check, .env creation, STEALTHLAB_MCP_TOKEN generation,
    docker compose up --build, and health verification.

    The one real decision this script cannot make for you: local models
    (free, via Ollama) vs. real API keys. You're asked once, up front.

    What this script does NOT do, honestly: it does not install Docker
    Desktop or Ollama for you if they're missing -- it detects that and
    tells you exactly what to install, then stops. Postgres+pgvector
    cannot come from pip/PowerShell; Docker is a real prerequisite.

.EXAMPLE
    .\install.ps1
#>

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$EnvFile = Join-Path $BackendDir ".env"
$EnvExample = Join-Path $BackendDir ".env.example"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "    $msg" -ForegroundColor Red }

# --- 1. Docker check -----------------------------------------------------
Write-Step "Checking Docker"
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Write-Fail "Docker is not installed."
    Write-Host "    Install Docker Desktop, then re-run this script: https://www.docker.com/products/docker-desktop/"
    exit 1
}
try {
    docker version --format '{{.Server.Version}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker daemon not responding" }
    Write-Ok "Docker is installed and running."
} catch {
    Write-Fail "Docker is installed but the daemon isn't running -- start Docker Desktop, then re-run this script."
    exit 1
}

# --- 2. .env bring-up ------------------------------------------------------
Write-Step "Setting up backend/.env"
if (-not (Test-Path $EnvExample)) {
    Write-Fail "backend/.env.example not found -- is this script running from the repo root?"
    exit 1
}
if (-not (Test-Path $EnvFile)) {
    Copy-Item $EnvExample $EnvFile
    Write-Ok "Created backend/.env from backend/.env.example"
} else {
    Write-Ok "backend/.env already exists -- leaving your existing values in place."
}

# --- 3. STEALTHLAB_MCP_TOKEN --------------------------------------------
Write-Step "Checking STEALTHLAB_MCP_TOKEN"
$envLines = Get-Content $EnvFile
$tokenLineIndex = -1
$tokenIsSet = $false
for ($i = 0; $i -lt $envLines.Count; $i++) {
    if ($envLines[$i] -match '^STEALTHLAB_MCP_TOKEN=(.*)$') {
        $tokenLineIndex = $i
        $tokenIsSet = -not [string]::IsNullOrWhiteSpace($Matches[1])
        break
    }
}
if ($tokenIsSet) {
    Write-Ok "STEALTHLAB_MCP_TOKEN is already set -- leaving it alone."
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $python) {
        Write-Fail "Python not found -- needed once to generate a secure token. Install Python 3.12+, then re-run."
        exit 1
    }
    $token = & $python.Source -c "import secrets; print(secrets.token_urlsafe(32))"
    if ($tokenLineIndex -ge 0) {
        $envLines[$tokenLineIndex] = "STEALTHLAB_MCP_TOKEN=$token"
    } else {
        $envLines += "STEALTHLAB_MCP_TOKEN=$token"
    }
    Set-Content -Path $EnvFile -Value $envLines -Encoding utf8
    Write-Ok "Generated and saved a new STEALTHLAB_MCP_TOKEN."
}

# --- 4. The one real decision: local models vs. real API keys -----------
Write-Step "Model source"
$envLines = Get-Content $EnvFile
$useLocalLine = ($envLines | Where-Object { $_ -match '^USE_LOCAL_MODELS=' })
$alreadyDecided = $useLocalLine -and ($useLocalLine -notmatch '^USE_LOCAL_MODELS=false\s*$')
$hasApiKey = ($envLines | Where-Object { $_ -match '^(ANTHROPIC|OPENAI|GENERAL_COMPUTE)_API_KEY=\S' })

if ($alreadyDecided -or $hasApiKey) {
    Write-Ok "Model source already configured in backend/.env -- leaving it alone."
} else {
    Write-Host "    How should StealthLab talk to a model?"
    Write-Host "      [1] Local, free, via Ollama (recommended for a personal instance)"
    Write-Host "      [2] I'll paste a real API key myself"
    $choice = Read-Host "    Choose 1 or 2"
    if ($choice -eq "1") {
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $ollama) {
            Write-Fail "Ollama not found. Install it from https://ollama.com, then re-run this script."
            exit 1
        }
        Write-Step "Pulling local models (this can take a while the first time)"
        foreach ($model in @("llama3.2", "qwen2.5", "mistral", "gemma2", "mxbai-embed-large")) {
            Write-Host "    ollama pull $model"
            & ollama pull $model
        }
        $envLines = $envLines -replace '^USE_LOCAL_MODELS=false\s*$', 'USE_LOCAL_MODELS=true'
        Set-Content -Path $EnvFile -Value $envLines -Encoding utf8
        Write-Ok "USE_LOCAL_MODELS=true set. Make sure 'ollama serve' is running before you use StealthLab."
    } else {
        Write-Warn "Open backend/.env now and paste at least one API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, or GENERAL_COMPUTE_API_KEY), then re-run this script."
        exit 0
    }
}

# --- 5. Bring the stack up -------------------------------------------------
Write-Step "Starting StealthLab (docker compose up -d --build)"
Push-Location $RepoRoot
try {
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
} finally {
    Pop-Location
}

# --- 6. Wait for health, then verify for real ----------------------------
Write-Step "Waiting for the backend to come up"
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        docker compose exec -T backend python scripts/migrate.py --status 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    } catch {}
}
if (-not $ready) {
    Write-Fail "Backend didn't report healthy migrations within 60s -- check 'docker compose logs backend'."
    exit 1
}
Write-Ok "Migrations applied."

try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8765/mcp" -Method Post -UseBasicParsing -SkipHttpErrorCheck
    if ($resp.StatusCode -eq 401) {
        Write-Ok "MCP server is up and the auth gate is enforced (401, as expected without a token)."
    } else {
        Write-Warn "MCP server responded with HTTP $($resp.StatusCode) -- expected 401. Check 'docker compose logs backend'."
    }
} catch {
    Write-Warn "Could not reach http://127.0.0.1:8765/mcp yet -- give it a few more seconds and check 'docker compose logs backend' if this persists."
}

# --- 7. Done ---------------------------------------------------------------
$savedToken = ((Get-Content $EnvFile) | Where-Object { $_ -match '^STEALTHLAB_MCP_TOKEN=(.+)$' } | Select-Object -First 1)
Write-Step "StealthLab is running"
Write-Host @"

    MCP endpoint: http://127.0.0.1:8765/mcp
    Your STEALTHLAB_MCP_TOKEN is saved in backend\.env.

    To use it from Claude Desktop or Claude Code, add an MCP server entry
    pointing at that URL with an Authorization: Bearer <token> header --
    see backend/README_MCP_SERVER.md for the exact client config.

    Stop it any time with:  docker compose down
    Start it again with:    docker compose up -d   (add --build after a git pull)

"@
