<#
Hand a disconnected RDP session back to the console, so the desktop keeps rendering.

WHY. The commissioner drives FBPB3 with real mouse clicks - it is a VB6 app whose owner-drawn
controls ignore posted messages - and real clicks need a desktop Windows is actually drawing.
Closing a Remote Desktop window LOCKS that session: the processes keep running, nothing renders,
and a sim fails at its next click. The console session never locks, and renders fine with no
monitor attached.

Run as SYSTEM by the scheduled task "Cheezeyverse session keeper", two seconds after Windows
logs event 24 (session disconnected). tscon needs SYSTEM; this cannot be a user task.

THIS FILE IS THE SCRIPT, not a template. Its predecessor was written line by line out of a .bat
with `echo`, and cmd treats ^ as its own escape character - so every ^ in the PowerShell silently
vanished on the way to disk. Both regex anchors were eaten (`'^\s*...'` became `'\s*...'`, which
can match from anywhere in a line and pick up the wrong columns) and the log text came out as
`-^> console`. A generated script that looks almost right is worse than one that fails, because
nobody reads a file they believe they know the contents of.
#>
param(
    [string]$User = $env:USERNAME,
    [string]$LogDir = 'C:\ProgramData\Cheezeyverse',
    # Say what it WOULD do and touch nothing. Testing this for real means disconnecting
    # somebody, which is not a thing a test gets to decide to do.
    [switch]$DryRun
)

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$log = Join-Path $LogDir 'console_handoff.log'

# Let the disconnect finish before asking what state the session is in.
Start-Sleep -Seconds 2

# qwinsta filtered to ONE user, rather than parsing the whole table. The full table has rows
# with an empty username (services, console), and those shift every column left - which is how
# a plausible-looking parse ends up calling tscon on session 0.
$id = $null
foreach ($line in (& "$env:windir\System32\qwinsta.exe" $User 2>$null)) {
    if ($line -match '\s(\d+)\s+Disc\b') { $id = $Matches[1]; break }
}

if ($id) {
    $out = & "$env:windir\System32\tscon.exe" $id /dest:console 2>&1
    $msg = "session $id moved to console (tscon exit $LASTEXITCODE) $out"
} else {
    $msg = "no disconnected session for $User - nothing to do"
}
Add-Content -Path $log -Value ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $msg)
