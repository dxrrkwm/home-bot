# stops the hub and removes the task; the laptop agent takes the bot over in 10 minutes.
# -Purge also deletes the folder with the settings (tokens) and the state
param([string]$Dir = "C:\home-bot", [switch]$Purge)
Unregister-ScheduledTask -TaskName "home-bot watchdog" -Confirm:$false -ea 0
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | ? { $_.CommandLine -match 'bot\.py' } | % { Stop-Process -Id $_.ProcessId -Force }
if ($Purge) { Remove-Item -Recurse -Force $Dir }
Write-Host "removed"
