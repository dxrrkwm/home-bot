# restart bot if it is dead, hung (no heartbeat) or eats too much memory
$dir = $PSScriptRoot
$python = if ($env:HOME_BOT_PYTHON) { $env:HOME_BOT_PYTHON } else { "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" }
function Write-Log($m) { Add-Content "$dir\bot.log" "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') watchdog: $m" -Encoding utf8 }

$bot = Get-CimInstance Win32_Process -Filter "Name like 'python%'" | ? { $_.CommandLine -match 'bot\.py' }
if ($bot) {
    $hb = Get-Item "$dir\heartbeat.txt" -ea 0
    $age = if ($hb) { ((Get-Date) - $hb.LastWriteTime).TotalMinutes } else { 0 }
    $mem = ($bot | Measure-Object PrivatePageCount -Sum).Sum / 1MB
    if ($age -lt 6 -and $mem -lt 500 -and @($bot).Count -eq 1) { exit }
    Write-Log ("restart: heartbeat {0:n0} min, {1:n0} MB, copies {2}" -f $age, $mem, @($bot).Count)
    $bot | % { Stop-Process -Id $_.ProcessId -Force -ea 0 }
    Start-Sleep 2
} else {
    Write-Log "bot was dead, restarting"
}
if ((Get-Item "$dir\bot_err.log" -ea 0).Length -gt 0) { Copy-Item "$dir\bot_err.log" "$dir\bot_err_$(Get-Date -Format yyyyMMdd_HHmm).log" }
Start-Process $python -ArgumentList '-X','faulthandler','bot.py' -WorkingDirectory $dir -WindowStyle Hidden -RedirectStandardError "$dir\bot_err.log" -RedirectStandardOutput "$dir\bot_out.log"
