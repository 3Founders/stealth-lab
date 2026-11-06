# kell desktop for Windows, one line:   irm https://www.kell.org.in/desktop.ps1 | iex
#
# Downloads the newest installer from kell.org.in/download, runs it silently (per user: no administrator prompt) and starts
# kell. Nothing else is changed on the machine. This is not the CLI installer (that is install.ps1).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # the progress bar makes Invoke-WebRequest very slow on Windows PowerShell 5.1
if ($env:OS -ne "Windows_NT") { throw "kell desktop installs on Windows only for now." }

# The www host, not the apex: kell.org.in answers with a 308 to www, and Windows PowerShell 5.1's Invoke-WebRequest does
# not follow a 308. The page's one-liner uses the same host (lib/install-commands.ts).
$site = if ($env:KELL_SITE) { $env:KELL_SITE.TrimEnd("/") } else { "https://www.kell.org.in" }
$file = Join-Path $env:TEMP "kell-Setup.exe"

Write-Host "Downloading kell ..."
Invoke-WebRequest -Uri "$site/download" -OutFile $file -UseBasicParsing
if ((Get-Item $file).Length -lt 10MB) { throw "The download is not the installer. Try again, or download it from $site/download in a browser." }

Write-Host "Installing kell ..."
Start-Process -FilePath $file -ArgumentList "/S" -Wait
Remove-Item $file -Force -ErrorAction SilentlyContinue

$exe = Join-Path $env:LOCALAPPDATA "Programs\kell-desktop\kell.exe"
if (Test-Path $exe) { Write-Host "Starting kell. Sign in when the window opens."; Start-Process $exe }
else { Write-Host "Installed. Open kell from the Start menu." }
