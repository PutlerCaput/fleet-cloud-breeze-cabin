$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Have-Node {
  return [bool](Get-Command node -ErrorAction SilentlyContinue)
}

if (-not (Have-Node)) {
  Write-Host "Node.js not found. Installing..."
  winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

if (-not (Have-Node)) {
  Write-Host "Install Node.js 22 from https://nodejs.org and run Setup-Koridor.bat again."
  exit 1
}

Write-Host "Installing dependencies..."
npm install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null
$secrets = Join-Path $Root "secrets.env"
if (-not (Test-Path $secrets)) {
  Copy-Item (Join-Path $Root "secrets.env.example") $secrets
  Write-Host "Created secrets.env. Add MEXC_API_KEY and MEXC_SECRET, then run Setup again."
  notepad $secrets
  exit 0
}

$runner = Join-Path $Root "run-koridor.cmd"
@(
  "@echo off",
  "cd /d `"%~dp0`"",
  "npm run dev >> data\windows.log 2>&1"
) | Set-Content -Path $runner -Encoding ASCII

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "Koridor" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "Koridor"

$exe = Join-Path $Root "Koridor.exe"
if (Test-Path $exe) {
  $startup = [Environment]::GetFolderPath("Startup")
  $lnk = Join-Path $startup "Koridor.lnk"
  $shell = New-Object -ComObject WScript.Shell
  $sc = $shell.CreateShortcut($lnk)
  $sc.TargetPath = $exe
  $sc.WorkingDirectory = $Root
  $sc.WindowStyle = 7
  $sc.Description = "Koridor control"
  $sc.Save()
  Start-Process -FilePath $exe -WorkingDirectory $Root
}

Write-Host "Koridor is installed."
Write-Host "The bot starts at Windows logon and restarts if it stops."
Write-Host "Desk: http://127.0.0.1:8080"
Write-Host "You can close this window."
