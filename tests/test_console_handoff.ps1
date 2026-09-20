$ErrorActionPreference = 'Stop'
$source = Get-Content (Join-Path $PSScriptRoot '..\tools\console_handoff.ps1') -Raw
$source = $source.Replace('& "$env:windir\System32\qwinsta.exe"', 'Get-TestSessions').Replace('& "$env:windir\System32\tscon.exe"', 'Invoke-TestHandoff')
function Invoke-TestHandoff { throw 'DryRun invoked tscon' }
function Get-TestSessions { ' testuser 12 Disc' }
$testDir = Join-Path ([IO.Path]::GetTempPath()) ('cv-dryrun-' + [guid]::NewGuid())
$result = & ([scriptblock]::Create($source)) -DryRun -User testuser -LogDir $testDir
if ($result -notmatch 'Would move session 12') { throw 'Did not report the planned session' }
if (Test-Path -LiteralPath $testDir) { throw 'DryRun created a log directory' }
function Get-TestSessions { ' testuser 12 Active' }
$result = & ([scriptblock]::Create($source)) -DryRun -User testuser -LogDir $testDir
if ($result -notmatch 'nothing to do') { throw 'Unexpected no-session result' }
if (Test-Path -LiteralPath $testDir) { throw 'DryRun wrote files' }
'OK: DryRun never hands off a session or writes logs'
