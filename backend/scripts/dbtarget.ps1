<#
.SYNOPSIS
  Point this PowerShell session's DB env at the local or hosted Postgres.

.DESCRIPTION
  DOT-SOURCE this (note the leading dot + space) so the variables land in
  your current session, not a child scope:

      . .\scripts\dbtarget.ps1 local     # everything now uses $DATABASE_URL_LOCAL
      . .\scripts\dbtarget.ps1 hosted    # back to Supabase
      . .\scripts\dbtarget.ps1 show      # what's active + what each resolves to
      . .\scripts\dbtarget.ps1 off       # clear the overrides

  After `. .\scripts\dbtarget.ps1 local` a bare `python scripts\migrate.py`
  or `python -m pytest tests -q` uses the local DB. The e2e suite picks it
  up via tests/conftest.py's TEST_DATABASE_URL promotion.

  Run from backend/ .  Needs DATABASE_URL_LOCAL in backend/.env (or the env).
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('local', 'hosted', 'show', 'off')]
    [string]$Target = 'show'
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$dbtarget = Join-Path $scriptDir 'dbtarget.py'

if ($Target -eq 'off') {
    Remove-Item Env:DATABASE_URL, Env:TEST_DATABASE_URL, Env:STEALTHLAB_DB_TARGET -ErrorAction SilentlyContinue
    Write-Host '[dbtarget] cleared DATABASE_URL / TEST_DATABASE_URL / STEALTHLAB_DB_TARGET for this session.'
    return
}

if ($Target -eq 'show') {
    & python $dbtarget show
    return
}

# Ask dbtarget.py for the export lines, then eval them into THIS session.
$lines = & python $dbtarget $Target --print-env --shell powershell
if ($LASTEXITCODE -ne 0) {
    Write-Error "[dbtarget] resolve failed for '$Target' (see message above)."
    return
}
$lines | ForEach-Object { Invoke-Expression $_ }
Write-Host "[dbtarget] session now points at: $Target"
& python $dbtarget show
