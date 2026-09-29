# ANDX Trading Bot - one-command installer (Windows PowerShell).
# Downloading via PowerShell avoids the SmartScreen "Windows protected your PC"
# prompt, because nothing is double-clicked from Explorer.
$ErrorActionPreference = "Stop"
$dir = "$HOME\andx-bot"
$zip = "$env:TEMP\andx-bot.zip"

# find Python
$py = $null
foreach ($c in @("py","python")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) {
  Write-Host ""
  Write-Host "Python 3 is required. Install it from https://www.python.org/downloads/"
  Write-Host "IMPORTANT: tick 'Add Python to PATH' during install, then run this command again."
  return
}

# Old Python cannot run this app (it needs 3.10+).
& $py -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "Your Python is too old (this bot needs Python 3.10 or newer)."
  Write-Host "Install the latest from https://www.python.org/downloads/ then run this command again."
  return
}

if (-not (Test-Path $dir)) {
  Write-Host "Downloading the ANDX Trading Bot..."
  Invoke-WebRequest "https://github.com/andxtrading/andx-bot/archive/refs/heads/main.zip" -OutFile $zip
  if (Test-Path "$env:TEMP\andx-extract") { Remove-Item "$env:TEMP\andx-extract" -Recurse -Force }
  Expand-Archive -Path $zip -DestinationPath "$env:TEMP\andx-extract" -Force
  Move-Item "$env:TEMP\andx-extract\andx-bot-main" $dir
  Write-Host "Setting up (about a minute)..."
  Set-Location $dir
  & $py -m venv .venv
  & ".\.venv\Scripts\pip.exe" install -q -r requirements.txt
} else {
  Write-Host "Bot already installed - starting it (your edits are kept)."
  Set-Location $dir
  if (-not (Test-Path ".venv")) { & $py -m venv .venv; & ".\.venv\Scripts\pip.exe" install -q -r requirements.txt }
}

Write-Host "Starting the bot..."
Start-Process -WindowStyle Hidden ".\.venv\Scripts\python.exe" "app.py"
Start-Sleep -Seconds 4
Start-Process "http://127.0.0.1:8300"
Write-Host ""
Write-Host "Done! The dashboard should be open in your browser."
Write-Host "Your bot files are in: $dir"
