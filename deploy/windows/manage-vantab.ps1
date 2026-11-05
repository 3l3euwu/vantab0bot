# ─────────────────────────────────────────────────────────────────────────────
#  Vantab · Windows helper
#
#  Registers a Scheduled Task that starts the bot when you log in, hides the
#  console, and lets run-vantab.bat restart the bot if it ever exits.
#
#  Install / update:
#      powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1
#  Start / stop / status:
#      powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Start
#      powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Stop
#      powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Status
#  Remove the task:
#      powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Uninstall
# ─────────────────────────────────────────────────────────────────────────────
[CmdletBinding()]
param(
    [string]$TaskName = "VantabBot",
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$launcher = Join-Path $PSScriptRoot "run-hidden.vbs"
$logFile = Join-Path $root "logs\bot.log"

function Stop-VantabProcess {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*" } |
        ForEach-Object {
            Write-Host "Stopping python (PID $($_.ProcessId))"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host "Task '$TaskName' is not installed. Run this script without arguments to install it."
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "Task      : $TaskName ($($task.State))"
        Write-Host "Last run  : $($info.LastRunTime)  result: $($info.LastTaskResult)"
        Write-Host "Next run  : $($info.NextRunTime)"
    }
    $running = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*" }
    if ($running) {
        Write-Host "Bot       : running ($($running.Count) python process(es))"
    } else {
        Write-Host "Bot       : not running"
    }
    if (Test-Path $logFile) {
        $size = [math]::Round((Get-Item $logFile).Length / 1KB, 1)
        Write-Host "Log       : $logFile ($size KB)"
    }
}

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Stop-VantabProcess
    Write-Host "Task '$TaskName' removed and the bot stopped."
    return
}

if ($Stop) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Stop-VantabProcess
    Write-Host "Vantab stopped. (It starts again at your next logon unless you -Uninstall.)"
    return
}

if ($Status) {
    Show-Status
    return
}

if ($Start) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $TaskName
    } else {
        Start-Process -FilePath "wscript.exe" -ArgumentList "`"$launcher`"" -WorkingDirectory $root
    }
    Write-Host "Vantab starting — check $logFile"
    return
}

# ── install ──────────────────────────────────────────────────────────────────
if (-not (Test-Path (Join-Path $root "bot.py"))) {
    throw "bot.py not found in $root — run this script from the repository, inside deploy\windows."
}
if (-not (Test-Path $launcher)) {
    throw "run-hidden.vbs is missing next to this script."
}
if (-not (Test-Path (Join-Path $root ".venv\Scripts\python.exe"))) {
    Write-Warning "No .venv found: the bot will run with the global 'python'. Recommended: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
}
if (-not (Test-Path (Join-Path $root "tkn.txt")) -and -not (Test-Path (Join-Path $root ".env"))) {
    Write-Warning "Neither tkn.txt nor .env was found: the bot will exit at startup with a missing-token error."
}

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$launcher`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Vantab Discord bot (starts at logon, restarts on crash)" -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Installed: the task '$TaskName' starts Vantab at every logon, hidden."
Write-Host "Logs     : $logFile"
Write-Host "Status   : powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Status"
Write-Host ""
Write-Host "Keep the PC awake while the bot must stay online (screen may sleep):"
Write-Host "  powercfg /change standby-timeout-ac 0"
Write-Host "  powercfg /change hibernate-timeout-ac 0"
