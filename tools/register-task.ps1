# the watchdog task: at startup and every 3 minutes, whether anyone is logged on or not (S4U, no password).
# S4U needs admin, install-hub.ps1 runs this elevated
param(
    [Parameter(Mandatory)][string]$Dir,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$User,
    [string]$Task = "home-bot watchdog"
)
$ErrorActionPreference = "Stop"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dir\watchdog.ps1`" -Python `"$Python`""
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 3))
)
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $Task
