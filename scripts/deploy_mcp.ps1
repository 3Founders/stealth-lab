# Deploy the MCP server (backend/Dockerfile.mcp-server) to Railway from a staged copy of a git ref.
#
# Why this script exists (2026-11-21): the handoff recipe was
#
#   git archive $H backend | tar -x -C $ST
#
# which is correct in bash and WRONG in PowerShell: PowerShell pipes the
# archive as text, tar reports "Damaged tar archive (bad header checksum)",
# and the stage is left with almost nothing. `railway up` then uploads an
# empty archive and the build fails with
#
#   couldn't locate the dockerfile at path backend/Dockerfile.mcp-server
#
# The service has no deployment after that, so its hostname answers 404
# ("Application not found") and the desktop reports "kell answered 404".
# This script writes the archive to a FILE and extracts from the file, then
# refuses to deploy unless the stage actually contains the Dockerfile.
#
# Usage (from anywhere):
#   powershell -File scripts\deploy_mcp.ps1              # deploy HEAD
#   powershell -File scripts\deploy_mcp.ps1 -Ref 379be63e
#   powershell -File scripts\deploy_mcp.ps1 -NoDeploy    # build + check the stage only
#
# Railway must have an active plan; a lapsed trial refuses the upload with
# "Your trial has expired" before any build starts.

[CmdletBinding()]
param(
  [string]$Ref = "HEAD",
  [string]$Service = "kell-mcp",
  [switch]$NoDeploy
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $env:TEMP "kell-mcp-stage"
$tar = Join-Path $env:TEMP "kell-mcp.tar"

Write-Host "repo:  $repo"
Write-Host "ref:   $Ref"
Write-Host "stage: $stage"

# Resolve the ref once so the Dockerfile and the archive come from the same commit.
$sha = (& git -C $repo rev-parse --verify "$Ref^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or -not $sha) { throw "cannot resolve ref '$Ref'" }
Write-Host "sha:   $sha"

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null
if (Test-Path $tar) { Remove-Item -Force $tar }

# FILE, not a pipe: the pipe is what corrupted the archive.
& git -C $repo archive --format=tar -o $tar $sha backend
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
Write-Host "tar:   $([math]::Round((Get-Item $tar).Length / 1MB, 1)) MB"

& tar -xf $tar -C $stage
if ($LASTEXITCODE -ne 0) { throw "tar extract failed" }

# The service's RAILWAY_DOCKERFILE_PATH is backend/Dockerfile.mcp-server; the
# root copy keeps the build working if that variable is ever unset.
& git -C $repo show "${sha}:backend/Dockerfile.mcp-server" | Set-Content -NoNewline -Encoding utf8 (Join-Path $stage "Dockerfile")
Remove-Item -Force (Join-Path $stage "backend\app\tool_routing\data\.gitignore") -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $stage "backend\conflict_candidates.json") -ErrorAction SilentlyContinue

# Refuse to upload a stage that cannot build. This is the check the failed
# deployment was missing.
$checks = [ordered]@{
  "backend/"                          = Test-Path (Join-Path $stage "backend")
  "backend/Dockerfile.mcp-server"     = Test-Path (Join-Path $stage "backend\Dockerfile.mcp-server")
  "Dockerfile"                        = Test-Path (Join-Path $stage "Dockerfile")
}
foreach ($k in $checks.Keys) {
  if (-not $checks[$k]) { throw "stage check failed: $k is missing" }
}
$files = (Get-ChildItem $stage -Recurse -File | Measure-Object).Count
if ($files -lt 500) { throw "stage check failed: only $files files (expected >500); the archive is truncated" }
Write-Host "stage: $files files, $([math]::Round(((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)) MB"

if ($NoDeploy) {
  Write-Host "NoDeploy set: stage is ready, nothing uploaded."
  exit 0
}

# --ci streams build logs and exits; the installed CLI has no -m/--message.
& railway up $stage --path-as-root --no-gitignore --service $Service --ci
exit $LASTEXITCODE
