# Local worker (Windows). Same worker as every provider.
#   $env:CONTROL_DATABASE_URL = "postgresql://..."   # disposable/local DB for trials
#   $env:STEALTHLAB_ENV = "STAGING"                  # TEST/STAGING relax the "provider must be configured" check
#   .\deploy\ingestion\local\run-worker.ps1 -Mode --once
param([string]$Mode = "--once")
Set-Location "$PSScriptRoot\..\..\..\backend"
python -m app.ingestion.worker $Mode
