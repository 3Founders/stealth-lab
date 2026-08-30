#requires -Version 5.1
<#
.SYNOPSIS
    Docker-free bring-up for a personal StealthLab instance.

.DESCRIPTION
    No Docker Desktop, anywhere. Two real decisions instead of Docker's
    one: where Postgres+pgvector runs, and which model source to use.
    Everything else is automated.

    Postgres choice:
      [1] Hosted (Neon/Supabase/etc.) -- paste a connection string.
          Zero local DB install of any kind; pgvector runs on their server.
      [2] WSL2 + native Linux Postgres -- fully local, no account needed,
          avoids Docker Desktop specifically (WSL2 is a free, built-in
          Windows feature, none of Docker Desktop's licensing/weight).

    The backend itself runs as a plain Python venv, not a container.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$EnvFile = Join-Path $BackendDir ".env"
$EnvExample = Join-Path $BackendDir ".env.example"
$VenvDir = Join-Path $BackendDir ".venv"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "    $msg" -ForegroundColor Red }

function Set-EnvValue($lines, $key, $value) {
    $idx = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$key=") { $idx = $i; break }
    }
    if ($idx -ge 0) { $lines[$idx] = "$key=$value" } else { $lines += "$key=$value" }
    return $lines
}

# --- 1. Python check ------------------------------------------------------
Write-Step "Checking Python"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Fail "Python 3.12+ not found. Install it from https://python.org, then re-run."
    exit 1
}
$pyVersion = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Ok "Found Python $pyVersion at $($python.Source)"
if ([version]$pyVersion -lt [version]"3.12") {
    Write-Fail "Python 3.12+ required (pyproject.toml), found $pyVersion. Install a newer Python, then re-run."
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
$envLines = Get-Content $EnvFile

# --- 3. Postgres source: the first real decision --------------------------
$currentDbUrl = ($envLines | Where-Object { $_ -match '^DATABASE_URL=(.+)$' })
$dbAlreadySet = $currentDbUrl -and ($currentDbUrl -notmatch 'postgresql://user:password@host')
if ($dbAlreadySet) {
    Write-Ok "DATABASE_URL is already configured in backend/.env -- leaving it alone."
} else {
    Write-Step "Where should Postgres+pgvector run?"
    Write-Host "    [1] Hosted (Neon, Supabase, etc.) -- paste a connection string. No local DB install."
    Write-Host "    [2] WSL2 + native Linux Postgres -- fully local, no account, avoids Docker Desktop."
    $dbChoice = Read-Host "    Choose 1 or 2"

    if ($dbChoice -eq "1") {
        Write-Host "    Get a free Postgres+pgvector project at https://neon.tech or https://supabase.com,"
        Write-Host "    then paste its connection string (postgresql://...) here."
        $connStr = Read-Host "    Connection string"
        if ([string]::IsNullOrWhiteSpace($connStr)) {
            Write-Fail "No connection string given. Re-run when you have one."
            exit 1
        }
        $envLines = Set-EnvValue $envLines "DATABASE_URL" $connStr
        Set-Content -Path $EnvFile -Value $envLines -Encoding utf8
        Write-Ok "DATABASE_URL saved."
    } elseif ($dbChoice -eq "2") {
        Write-Step "Checking WSL2"
        $wslStatus = wsl --status 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "WSL2 isn't installed. Run 'wsl --install -d Ubuntu' in an ADMIN PowerShell, reboot, then re-run this script."
            exit 1
        }
        $distros = (wsl -l -q 2>&1) -replace "`0", ""
        if (-not ($distros -match "Ubuntu")) {
            Write-Fail "No Ubuntu WSL distro found. Run 'wsl --install -d Ubuntu' in an ADMIN PowerShell, reboot, then re-run this script."
            exit 1
        }
        Write-Ok "WSL2 + Ubuntu found."

        Write-Step "Installing Postgres + pgvector inside WSL2 (one-time; asks for your WSL sudo password)"
        $setupScript = @'
set -e
sudo apt-get update -qq
sudo apt-get install -y -qq postgresql postgresql-contrib postgresql-16-pgvector 2>/dev/null || \
  sudo apt-get install -y -qq postgresql postgresql-contrib
sudo service postgresql start
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='stealthlab'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER stealthlab WITH PASSWORD 'stealthlab' SUPERUSER;"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='stealthlab'" | grep -q 1 || \
  sudo -u postgres createdb -O stealthlab stealthlab
sudo -u postgres psql -d stealthlab -c "CREATE EXTENSION IF NOT EXISTS vector;"
'@
        $setupScript | wsl -d Ubuntu -- bash -
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "WSL Postgres setup failed. If the pgvector apt package wasn't found for your Ubuntu release, install it manually inside WSL: see https://github.com/pgvector/pgvector#installation-notes-windows-subsystem-for-linux-wsl"
            exit 1
        }
        # WSL2's default (NAT) networking forwards localhost automatically for
        # most setups; if this doesn't resolve, `wsl hostname -I`'s address is
        # the real fallback -- noted in the final printout, not guessed here.
        $connStr = "postgresql://stealthlab:stealthlab@localhost:5432/stealthlab"
        $envLines = Set-EnvValue $envLines "DATABASE_URL" $connStr
        Set-Content -Path $EnvFile -Value $envLines -Encoding utf8
        Write-Ok "Postgres+pgvector running inside WSL2. DATABASE_URL saved."
    } else {
        Write-Fail "Invalid choice."
        exit 1
    }
}

# --- 4. STEALTHLAB_MCP_TOKEN ----------------------------------------------
Write-Step "Checking STEALTHLAB_MCP_TOKEN"
$envLines = Get-Content $EnvFile
$tokenLine = $envLines | Where-Object { $_ -match '^STEALTHLAB_MCP_TOKEN=(.*)$' }
if ($tokenLine -and $Matches -and -not [string]::IsNullOrWhiteSpace($Matches[1])) {
    Write-Ok "STEALTHLAB_MCP_TOKEN already set."
} else {
    $token = & $python.Source -c "import secrets; print(secrets.token_urlsafe(32))"
    $envLines = Set-EnvValue $envLines "STEALTHLAB_MCP_TOKEN" $token
    Set-Content -Path $EnvFile -Value $envLines -Encoding utf8
    Write-Ok "Generated and saved a new STEALTHLAB_MCP_TOKEN."
}

# --- 5. Model source: the second real decision -----------------------------
Write-Step "Model source"
$envLines = Get-Content $EnvFile
$useLocalLine = $envLines | Where-Object { $_ -match '^USE_LOCAL_MODELS=' }
$alreadyDecided = $useLocalLine -and ($useLocalLine -notmatch '^USE_LOCAL_MODELS=false\s*$')
$hasApiKey = $envLines | Where-Object { $_ -match '^(ANTHROPIC|OPENAI|GENERAL_COMPUTE)_API_KEY=\S' }
if ($alreadyDecided -or $hasApiKey) {
    Write-Ok "Model source already configured -- leaving it alone."
} else {
    Write-Host "    [1] Local, free, via Ollama"
    Write-Host "    [2] I'll paste a real API key myself"
    $choice = Read-Host "    Choose 1 or 2"
    if ($choice -eq "1") {
        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $ollama) {
            Write-Fail "Ollama not found. Install it from https://ollama.com, then re-run."
            exit 1
        }
        foreach ($model in @("llama3.2", "qwen2.5", "mistral", "gemma2", "mxbai-embed-large")) {
            Write-Host "    ollama pull $model"
            & ollama pull $model
        }
        $envLines = $envLines -replace '^USE_LOCAL_MODELS=false\s*$', 'USE_LOCAL_MODELS=true'
        Set-Content -Path $EnvFile -Value $envLines -Encoding utf8
        Write-Ok "USE_LOCAL_MODELS=true set. Make sure 'ollama serve' is running before you use StealthLab."
    } else {
        Write-Warn "Open backend/.env and paste an API key, then re-run this script."
        exit 0
    }
}

# --- 6. Python venv + install ----------------------------------------------
Write-Step "Setting up the Python virtual environment"
if (-not (Test-Path $VenvDir)) {
    & $python.Source -m venv $VenvDir
    Write-Ok "Created backend/.venv"
} else {
    Write-Ok "backend/.venv already exists."
}
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e $BackendDir
Write-Ok "Backend dependencies installed."

# --- 7. Migrate + run --------------------------------------------------------
Write-Step "Applying database migrations"
Push-Location $BackendDir
try {
    & $venvPython scripts\migrate.py
    & $venvPython scripts\migrate.py --status
    if ($LASTEXITCODE -ne 0) { throw "migration status check failed" }
} finally {
    Pop-Location
}
Write-Ok "Migrations applied."

Write-Step "Starting the MCP server (background)"
Push-Location $BackendDir
$proc = Start-Process -FilePath $venvPython `
    -ArgumentList "-m", "uvicorn", "app.mcp_server.server:app", "--host", "127.0.0.1", "--port", "8765" `
    -PassThru -WindowStyle Hidden
Pop-Location
Start-Sleep -Seconds 3

try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8765/mcp" -Method Post -UseBasicParsing -SkipHttpErrorCheck
    if ($resp.StatusCode -eq 401) {
        Write-Ok "MCP server is up and the auth gate is enforced (401, as expected without a token)."
    } else {
        Write-Warn "MCP server responded with HTTP $($resp.StatusCode) -- expected 401."
    }
} catch {
    Write-Warn "Could not reach http://127.0.0.1:8765/mcp yet -- it may still be starting (PID $($proc.Id))."
}

Write-Step "StealthLab is running (no Docker) -- PID $($proc.Id)"
Write-Host @"

    MCP endpoint: http://127.0.0.1:8765/mcp
    Your STEALTHLAB_MCP_TOKEN is saved in backend\.env.
    Process ID: $($proc.Id)  (stop it with: Stop-Process -Id $($proc.Id))

    If you chose WSL2 and localhost doesn't connect, run 'wsl hostname -I'
    inside WSL and use that IP in DATABASE_URL instead of 'localhost'.

"@
