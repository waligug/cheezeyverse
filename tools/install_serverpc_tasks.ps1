<#
Make the universe survive a reboot. Run once, on the server, as the user that runs the sims.

WHAT IT REGISTERS, both as ORDINARY USER TASKS - no administrator, no SYSTEM:

  Cheezeyverse panel           at logon, keeps tools\run_panel.ps1 running, which keeps the
                               commissioner panel running.
  Cheezeyverse offsite backup  daily, copies the three league.dat files and the keys to
                               D:\Cheezeyverse-backups and verifies every copy by hash.

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
    [string]$BackupAt = '05:00'
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

Install-Task -name 'Cheezeyverse panel' `
    -action (New-ScheduledTaskAction -Execute $ps `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Repo\tools\run_panel.ps1`"" `
        -WorkingDirectory $Repo) `
    -trigger (New-ScheduledTaskTrigger -AtLogOn -User $me) `
    -description 'Keeps the commissioner panel (port 5095) running, and brings it back after a reboot. See tools\run_panel.ps1.'

Install-Task -name 'Cheezeyverse offsite backup' `
    -action (New-ScheduledTaskAction -Execute $python `
        -Argument "tools\offsite_backup.py" -WorkingDirectory $Repo) `
    -trigger (New-ScheduledTaskTrigger -Daily -At $BackupAt) `
    -description 'Copies league.dat and the keys to D:\Cheezeyverse-backups, verified by hash. See tools\offsite_backup.py.'

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
