<#
Keep the commissioner panel running, across crashes and across reboots.

WHY THIS EXISTS. The panel is how the universe is simmed, and for its whole life it has been
started by hand - or, worse, started as a child of whatever terminal or agent session happened
to be open at the time. That process dies with its parent. Nobody notices until the next time
somebody opens the panel and finds nothing listening, which on a machine that is administered
remotely can be days.

WHAT IT DOES. Starts the panel, waits for it, starts it again if it stops, and writes down what
happened. Registered by tools\install_serverpc_tasks.ps1 as a logon task, so a reboot brings the
panel back on its own.

WHAT IT WILL NOT DO IS START A SECOND ONE. Two panels on one port is not a doubled service, it
is one working panel and one that failed to bind and exited - and if the running one is a sim in
progress, a supervisor that kept trying would spend the whole sim logging failures. If the port
already answers, this waits and checks again.

    powershell -NoProfile -ExecutionPolicy Bypass -File tools\run_panel.ps1
#>
param(
    [int]$Port = 5095,
    [string]$Repo = 'C:\claude\hoops-universe',
    [string]$Python = '',
    [string]$LogDir = (Join-Path $env:LOCALAPPDATA 'Cheezeyverse')
)

$ErrorActionPreference = 'Continue'
if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    $Python = if ($cmd) { $cmd.Source } else { 'python' }
}
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$log = Join-Path $LogDir 'panel.log'
$outLog = Join-Path $LogDir 'panel.out.log'
$errLog = Join-Path $LogDir 'panel.err.log'

# PROVE THE LOG IS WRITABLE BEFORE ANYTHING ELSE, and refuse to run if it is not.
#
# The first version of this defaulted to C:\ProgramData\Cheezeyverse, which an elevated
# installer had created months earlier and which an ordinary user cannot write to. Every log
# line went into a swallowed catch, the panel's own redirect could not open its target either,
# and the supervisor sat in a restart loop starting nothing - task State "Running", panel dead,
# not one line anywhere saying why. A supervisor that cannot say what it is doing is worse than
# no supervisor, so this now dies loudly instead.
try {
    Add-Content -Path $log -Value ("{0:yyyy-MM-dd HH:mm:ss}  ----" -f (Get-Date)) -Encoding utf8 -ErrorAction Stop
} catch {
    Write-Error "cannot write $log - the supervisor would run blind. $($_.Exception.Message)"
    exit 2
}

function Write-Line($message) {
    $line = "{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $message
    try { Add-Content -Path $log -Value $line -Encoding utf8 } catch { Write-Error $line }
}

function Send-Alarm($message) {
    <#
      Say out loud that the panel will not start. Everything else here recovers by itself; this
      is the one state a person has to look at, and without it the failure is a log file on a
      machine in another room that nobody has a reason to open.

      The webhook is read from .env at the moment it is needed and never written anywhere - not
      to the log, not to an error. Throttled to once an hour, because the watchdog restarts this
      script every five minutes and a broken panel would otherwise post all night.
    #>
    $stamp = Join-Path $LogDir 'last-alarm.txt'
    try {
        if (Test-Path $stamp) {
            $last = [datetime](Get-Content $stamp -Raw).Trim()
            if ((Get-Date) - $last -lt (New-TimeSpan -Hours 1)) { return }
        }
    } catch {}
    $hook = $null
    try {
        foreach ($line in (Get-Content (Join-Path $Repo '.env') -ErrorAction Stop)) {
            if ($line -match '^\s*DISCORD_WEBHOOK_URL\s*=\s*(\S+)') { $hook = $Matches[1].Trim('"''') }
        }
    } catch {}
    if (-not $hook) { return }
    try {
        Invoke-RestMethod -Uri $hook -Method Post -TimeoutSec 10 -ContentType 'application/json' `
            -Body (@{ content = $message } | ConvertTo-Json -Compress) | Out-Null
        Set-Content -Path $stamp -Value (Get-Date).ToString('o')
        Write-Line "posted an alarm to Discord"
    } catch {
        # Never print the exception: a failed Invoke-RestMethod puts the URL in its message.
        Write-Line "could not post the alarm to Discord ($($_.Exception.GetType().Name))"
    }
}

function Test-PanelUp {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 5 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

# Keep the log from growing without limit. It is one line per start and one per stop, so this
# is years of restarts, but a crash loop writes fast and an unbounded log on the system drive is
# its own outage.
try {
    if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
        Move-Item -Path $log -Destination "$log.1" -Force
    }
} catch {}

Write-Line "supervisor starting (port $Port, repo $Repo, python $Python)"
Set-Location $Repo
$shortRuns = 0

while ($true) {
    if (Test-PanelUp) {
        # Somebody else's panel is serving - an agent session, or a hand-started one. Leave it
        # alone and keep watching, so this takes over the moment that one goes away.
        Start-Sleep -Seconds 30
        continue
    }
    Write-Line "starting the panel"
    $started = Get-Date
    # KEEP THE LAST RUN'S OUTPUT. Start-Process truncates what it redirects to, and the output
    # you want most is the output of the run that just died, so it is moved aside first.
    foreach ($f in @($outLog, $errLog)) {
        if ((Test-Path $f) -and (Get-Item $f).Length -gt 0) { Move-Item $f "$f.prev" -Force }
    }
    try {
        # Start-Process rather than the call operator, because `*>>` in Windows PowerShell 5.1
        # writes the file as UTF-16 and wraps every line a native program sends to stderr in an
        # ErrorRecord - and Flask logs to stderr, so the whole panel log came out as mangled
        # wide characters inside error formatting. This hands the file handles straight to
        # python and the bytes land as python wrote them.
        # -WindowStyle Hidden rather than -NoNewWindow, so THE PANEL DOES NOT DIE WITH THIS
        # SCRIPT. Sharing a console ties the two together: when the supervisor was killed on
        # 2026-09-20 it took a perfectly healthy panel down with it, and a sim running at that
        # moment would have gone too. Its own console means a dead supervisor is a five-minute
        # gap in supervision, not an outage.
        $proc = Start-Process -FilePath $Python `
            -ArgumentList '-u', '-m', 'commissioner.app', '--lan' `
            -WorkingDirectory $Repo -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $proc.WaitForExit()
        Write-Line "python exited with code $($proc.ExitCode)"
    } catch {
        Write-Line "the panel could not be started: $($_.Exception.Message)"
    }
    $ran = [int]((Get-Date) - $started).TotalSeconds
    Write-Line "the panel stopped after ${ran}s"
    # A panel that dies immediately is misconfigured, not unlucky: a missing package, a bad
    # .env, a port held by something else. Backing off turns a spin into a log somebody can
    # read, and leaves the machine usable while they read it.
    if ($ran -lt 10) {
        $shortRuns += 1
        # And after enough of them, STOP and let the failure be visible. Task Scheduler records
        # a non-zero result and server_doctor reports the task as failed; a supervisor that
        # retries forever reports "Running" for a panel that has never once come up.
        if ($shortRuns -ge 10) {
            Write-Line "the panel has failed to stay up $shortRuns times - giving up so the failure is visible"
            Send-Alarm ("**The commissioner panel will not start.** $shortRuns attempts in a row " +
                        "on SERVERPC, each lasting under ten seconds. The last error is in " +
                        "``%LOCALAPPDATA%\Cheezeyverse\panel.err.log`` on the server. Sims and " +
                        "the panel are both down until somebody looks.")
            exit 1
        }
        Write-Line "it did not stay up ($shortRuns in a row) - waiting 60s before trying again"
        Start-Sleep -Seconds 60
    } else {
        $shortRuns = 0
        Start-Sleep -Seconds 5
    }
}
