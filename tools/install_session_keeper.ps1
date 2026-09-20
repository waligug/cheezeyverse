<#
Register the task that keeps the server's desktop clickable after you disconnect - and PROVE it
registered, instead of hoping.

WHAT IT INSTALLS. "Cheezeyverse session keeper": a SYSTEM task that fires on event 24 (a Remote
Desktop session disconnected) and runs tools\console_handoff.ps1, which hands that session back
to the console. The console session never locks, so a sim in progress keeps being clickable.

WHY THIS REPLACED THE .BAT. The batch version wrote its helper script with `echo` and registered
the task with schtasks.exe. Both halves failed quietly:

  * cmd treats ^ as an escape character, so every ^ in the generated PowerShell was eaten. The
    helper's two regex anchors went with them.
  * the registration failed and left nothing behind but a "could not register" line in a window
    that closes. Run as administrator on 2026-09-20 it wrote the helper and registered no task,
    and the only way anybody found out was going looking for the task a second time.

So this registers through Register-ScheduledTask, which reports a real error, and then RUNS the
task and reads its log to show it actually worked. It elevates itself, so double-clicking is
enough.

    powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_session_keeper.ps1
#>
param(
    [string]$User = $env:USERNAME,
    [string]$TaskName = 'Cheezeyverse session keeper',
    [switch]$Pause
)

$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'console_handoff.ps1'
$logDir = 'C:\ProgramData\Cheezeyverse'
$log = Join-Path $logDir 'console_handoff.log'

function Am-I-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Am-I-Admin)) {
    Write-Output "Registering a SYSTEM task needs administrator - asking for it now."
    $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -User `"$User`" -Pause"
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $args
    return
}

Write-Output "user   : $User"
Write-Output "script : $script"
if (-not (Test-Path $script)) { throw "cannot find $script - run this from the repo's tools folder" }
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# The trigger is an event subscription, which New-ScheduledTaskTrigger cannot express, so the
# CIM class is used directly. Event 24 of the TerminalServices LocalSessionManager channel is
# "session has been disconnected".
$class = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$trigger = New-CimInstance -CimClass $class -ClientOnly
$trigger.Enabled = $true
$trigger.Subscription = @'
<QueryList><Query Id="0" Path="Microsoft-Windows-TerminalServices-LocalSessionManager/Operational"><Select Path="Microsoft-Windows-TerminalServices-LocalSessionManager/Operational">*[System[Provider[@Name='Microsoft-Windows-TerminalServices-LocalSessionManager'] and EventID=24]]</Select></Query></QueryList>
'@

$action = New-ScheduledTaskAction -Execute "$env:windir\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -User `"$User`""
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description 'On RDP disconnect, hands the session back to the console so FBPB3 stays clickable. See tools\console_handoff.ps1.' | Out-Null

# ---- prove it ------------------------------------------------------------------------------
# Registering and verifying are different things, and this task has now gone missing twice.
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { throw "Register-ScheduledTask reported success but the task is not there" }
Write-Output "registered: $($task.TaskName) [$($task.State)] as $($task.Principal.UserId)"

# Run it once, right now. Nothing is disconnected, so the correct outcome is the script saying
# there was nothing to do - which proves the task runs, SYSTEM can read the script, and the log
# is writable. All three have failed before.
$before = if (Test-Path $log) { (Get-Item $log).Length } else { 0 }
Start-ScheduledTask -TaskName $TaskName
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if ((Test-Path $log) -and (Get-Item $log).Length -gt $before) { break }
}
$line = if (Test-Path $log) { Get-Content $log -Tail 1 } else { $null }
if ((Test-Path $log) -and (Get-Item $log).Length -gt $before) {
    Write-Output "verified  : it ran, and wrote -> $line"
    Write-Output ""
    Write-Output "Done. Close the Remote Desktop window whenever you like; a couple of seconds"
    Write-Output "later the session moves to the console, where it never locks."
} else {
    Write-Output "WARNING   : the task is registered but produced no log line in 30s."
    Write-Output "            Check $log and the task's Last Run Result in Task Scheduler."
}
Write-Output ""
Write-Output "While a sim is running and you ARE connected: do not minimize the Remote Desktop"
Write-Output "window - a minimized mstsc stops the session rendering and the clicks stop landing."
if ($Pause) { Read-Host "Press Enter to close" }
