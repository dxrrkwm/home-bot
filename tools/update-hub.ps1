# pulls the new version and restarts the bot (the same as "/update" in the chat)
param([string]$Dir = "C:\home-bot")
$ErrorActionPreference = "Stop"
git -C $Dir pull --ff-only
$python = ((Get-ScheduledTask "home-bot watchdog").Actions[0].Arguments -replace '.*-Python "([^"]+)".*', '$1')
& $python -m pip install -q -r "$Dir\requirements.txt"
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | ? { $_.CommandLine -match 'bot\.py' } | % { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask "home-bot watchdog"
Start-Sleep 15
Get-Content "$Dir\bot.log" -Tail 5
