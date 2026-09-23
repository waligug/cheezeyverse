<#
Make the universe survive a reboot. Run once, on the server, as the user that runs the sims.

WHAT IT REGISTERS, all as ORDINARY USER TASKS - no administrator, no SYSTEM:

  Cheezeyverse panel           at logon, keeps tools\run_panel.ps1 running, which keeps the
                               commissioner panel running.
  Cheezeyverse offsite backup  daily, copies the three league.dat files and the keys to
                               D:\Cheezeyverse-backups and verifies every copy by hash.
  Cheezeyverse autopilot       ONLY with -Autopilot. Weekly, runs one Sim Week unattended and
                               posts the result. It never rolls a season over: at the season
                               end it posts the offseason preview and stops.

WHY AT LOGON AND NOT AT BOOT. The panel drives FBPB3 through real mouse clicks, so it has to
live in the interactive desktop session - a service or a SYSTEM task has no desktop to click on
and every sim would fail at its first click. This machine logs itself in (AutoAdminLogon), so a
logon trigger and a boot trigger amount to the same thing here, and only one of them can
actually run the game.

WHAT THIS DOES NOT DO, because it cannot without administrator rights: register the task that
hands a disconnected RDP session back to the console. Without that task, closing a Remote
Desktop window LOCKS the session, a locked session renders nothing, and a sim running at that
moment dies at its next click. That one is tools\install_session_keeper.bat, right-clicked and
Run as administrator. This script says so at the end if it is missing.

    powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_serverpc_tasks.ps1
#>
param(
    [string]$Repo = 'C:\claude\hoops-universe',
    [string]$BackupAt = '05:00',
    [switch]$Autopilot,
    [string]$AutopilotDay = 'Sunday',
    [string]$AutopilotAt = '04:00'
)

$ErrorActionPreference = 'Stop'
$me = "$env:USERDOMAIN\$env:USERNAME"
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "python is not on PATH for $me - the tasks would fail the same way" }
$ps = "$env:windir\System32\WindowsPowerShell\v1.0\powershell.exe"

Write-Output "user   : $me"
Write-Output "python : $python"
Write-Output "repo   : $Repo`n"

function Install-Task($name, $action, $trigger, $description) {
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    # -RunLevel Limited on purpose: neither of these needs administrator, and a task that asks
    # for rights it does not need is a task that stops working the day somebody tightens UAC.
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Description $description -Force | Out-Null
    $info = Get-ScheduledTask -TaskName $name
    Write-Output ("installed: {0}  [{1}]" -f $name, $info.State)
}

# AT LOGON, AND THEN EVERY FIVE MINUTES FOREVER.
#
# The logon trigger alone was not enough, and how that was found is why the extra lines are
# worth it: on 2026-09-20 the supervisor was killed by something outside itself - exit code
# 0xC000013A, a terminated process, with no line in its own log - and Task Scheduler's
# "restart on failure" did not bring it back. The panel was down for twenty minutes and the
# only reason anybody noticed was a test that went looking for something else.
#
# A repeating trigger does not care what killed it. Every five minutes it tries to start the
# task; MultipleInstances=IgnoreNew makes that a no-op while it is already running, so the
# steady state costs nothing and the failure state lasts at most five minutes. That is worth
# more than knowing who the killer was.
#
# TWO SEPARATE TRIGGERS, not one with a repetition hung off it. A repetition attached to the
# logon trigger only starts counting when that trigger FIRES - so on a machine that is already
# logged in, which is every machine you are fixing this on, the watchdog is armed no earlier
# than the next reboot. The one that matters would have been the one not running.
#
# AND THE WATCHDOG IS THE ONE THAT ACTUALLY WORKS. Measured on a real reboot, 2026-09-20: the
# machine came up at 11:40:35, auto-logged itself in, and THE LOGON TRIGGER DID NOT FIRE - the
# task's first run afterwards was the watchdog tick at 11:43:39. A logon trigger is missed when
# the Task Scheduler service is still starting as the logon completes, and StartWhenAvailable
# does not rescue it because that setting only applies to time-based triggers. So the interval
# is ONE minute, not five: a tick costs nothing while the task is already running (IgnoreNew
# records it and moves on), and it is the difference between a minute of no panel after a
# reboot and three.
$panelTriggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $me),
    (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650))
)

Install-Task -name 'Cheezeyverse panel' `
    -action (New-ScheduledTaskAction -Execute $ps `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Repo\tools\run_panel.ps1`"" `
        -WorkingDirectory $Repo) `
    -trigger $panelTriggers `
    -description 'Keeps the commissioner panel (port 5095) running: at logon, and re-checked every five minutes. See tools\run_panel.ps1.'

Install-Task -name 'Cheezeyverse offsite backup' `
    -action (New-ScheduledTaskAction -Execute $python `
        -Argument "tools\offsite_backup.py" -WorkingDirectory $Repo) `
    -trigger (New-ScheduledTaskTrigger -Daily -At $BackupAt) `
    -description 'Copies league.dat and the keys to D:\Cheezeyverse-backups, verified by hash. See tools\offsite_backup.py.'

# OFF BY DEFAULT, and that is deliberate. A scheduled Sim Week is the difference between a
# universe that runs and one that waits for somebody to remember it - but it drives the game
# through real mouse clicks on the interactive desktop, so it must never start while a person
# is mid-anything. Install it when you want it, with -Autopilot, and pick the hour.
#
# It is a SIM WEEK ONLY. autopilot.py refuses to roll a season over; when the regular season
# runs out it posts the offseason preview and stops. Nothing irreversible happens unattended.
if ($Autopilot) {
    if ($AutopilotDay -notin @('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')) {
        throw "-AutopilotDay must be a day name, got '$AutopilotDay'"
    }
    # ITS OWN SETTINGS, not the shared ones, and every difference is deliberate.
    #
    #  -RestartCount 0   autopilot.py's whole contract is that it NEVER retries: a week that
    #                    died halfway leaves a recovery journal, and a second attempt against an
    #                    unreconciled universe turns one bad night into two. The shared settings
    #                    say RestartCount 3 / 1 minute, which would have relaunched a failed week
    #                    three times at one-minute intervals.
    #  ExecutionTimeLimit  a run parked on a modal would otherwise hold the OS save lock for
    #                    ever while IgnoreNew silently discarded every following week. Four
    #                    hours is far longer than the ~11 minutes a week takes, and it ends.
    #  -StartWhenAvailable is OFF. It exists to run a missed task late - and late here means
    #                    driving real mouse clicks across somebody's desktop on Monday morning.
    #                    A missed week is a missed week; it waits for its own slot.
    $apSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -RestartCount 0 `
        -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    $apPrincipal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    # -u and a log file: readiness rows, every sim step, tracebacks and autopilot's own
    # "could not post to Discord" all went to a detached console and vanished. On the one night
    # Discord is also down that left no record anywhere. run_panel.ps1 sets the precedent.
    $apLog = Join-Path $env:LOCALAPPDATA 'Cheezeyverseutopilot.log'
    $null = New-Item -ItemType Directory -Force -Path (Split-Path $apLog) -ErrorAction SilentlyContinue
    $apAction = New-ScheduledTaskAction -Execute $ps `
        -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command " + `
                   "`"& '$python' -u toolsutopilot.py *>> '$apLog'`"") `
        -WorkingDirectory $Repo
    Register-ScheduledTask -TaskName 'Cheezeyverse autopilot' -Action $apAction `
        -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $AutopilotDay -At $AutopilotAt) `
        -Settings $apSettings -Principal $apPrincipal -Force `
        -Description "Runs one Sim Week unattended and posts the result. Never rolls a season over - at the season end it posts the offseason preview and stops. Logs to $apLog. See toolsutopilot.py." | Out-Null
    Write-Output ("installed: {0}  [{1}]  {2} {3}, log {4}" -f 'Cheezeyverse autopilot',
        (Get-ScheduledTask -TaskName 'Cheezeyverse autopilot').State, $AutopilotDay, $AutopilotAt, $apLog)
} else {
    # WITHOUT -Autopilot, REMOVE IT. Register-ScheduledTask -Force only ever adds; re-running
    # the installer plainly used to print "skipped" while the task stayed registered and kept
    # firing every Sunday. "Not installed" has to mean not installed.
    $existing = Get-ScheduledTask -TaskName 'Cheezeyverse autopilot' -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName 'Cheezeyverse autopilot' -Confirm:$false
        Write-Output "removed  : Cheezeyverse autopilot  (pass -Autopilot to keep it)"
    } else {
        Write-Output "skipped  : Cheezeyverse autopilot  (pass -Autopilot to install it)"
    }
}

Write-Output ""
$keeper = Get-ScheduledTask | Where-Object { $_.TaskName -match 'session keeper|keep desktop' }
if ($keeper) {
    Write-Output "ok: the RDP session keeper is installed ($($keeper.TaskName))"
} else {
    Write-Output "STILL MISSING, and it needs administrator:"
    Write-Output "  the task that hands a disconnected RDP session back to the console."
    Write-Output "  Without it, closing the Remote Desktop window locks the session, and a sim"
    Write-Output "  running at that moment fails at its next click."
    Write-Output "  Fix: right-click tools\install_session_keeper.bat -> Run as administrator"
}
Write-Output ""
Write-Output "Check the whole picture any time with:  python tools\server_doctor.py"
