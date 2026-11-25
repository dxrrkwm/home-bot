# installs home-bot as the hub on this PC: python, git, the repo, settings from the bundle, the watchdog task
# powershell -ExecutionPolicy Bypass -File install-hub.ps1 -Bundle C:\path\hub.bundle [-Dir C:\home-bot]
param(
    [Parameter(Mandatory)][string]$Bundle,
    [string]$Dir = "C:\home-bot",
    [string]$Repo = "https://github.com/dxrrkwm/home-bot.git"
)
$ErrorActionPreference = "Stop"
$task = "home-bot watchdog"

function Find-Python {
    $p = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python31[2-9]\python.exe", "C:\Program Files\Python31[2-9]\python.exe" -ea 0 |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($p) { $p.FullName }
}

if (-not (Find-Python)) { winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements }
if (-not (Get-Command git -ea 0)) {
    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
    $env:Path += ";C:\Program Files\Git\cmd"
}
$python = Find-Python
if (-not $python) { throw "python not found after install, open a new terminal and run again" }

if (Test-Path "$Dir\.git") { git -C $Dir pull --ff-only } else { git clone $Repo $Dir }
& $python -m pip install -q -r "$Dir\requirements.txt"

Push-Location $Dir
& $python -m app.bundle unpack $Bundle   # asks for the passphrase
if ($LASTEXITCODE) { Pop-Location; throw "bundle not unpacked" }
& $python -m app.role hub
Pop-Location

# every 3 minutes and at startup, whether anyone is logged on or not (S4U needs no password)
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dir\watchdog.ps1`" -Python `"$python`""
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 3))
)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $task

$tz = Get-TimeZone
if ($tz.BaseUtcOffset.TotalHours -ne 2) { Write-Warning "timezone $($tz.Id): reminders use local time, Kyiv is UTC+2" }
$sleep = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String "AC Power Setting Index") -replace '.*: ', ''
if ($sleep -and [Convert]::ToInt32($sleep, 16) -ne 0) { Write-Warning "this PC goes to sleep on idle, the bot sleeps with it" }

Start-Sleep 20
Get-Content "$Dir\bot.log" -Tail 5
Write-Host "done: $Dir, task '$task'"
