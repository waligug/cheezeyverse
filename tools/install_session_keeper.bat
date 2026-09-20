@echo off
REM ===================================================================================
REM  Double-click me (or right-click -> Run as administrator).
REM
REM  This used to BE the installer, and it was a bad one. It wrote its PowerShell helper
REM  line by line with `echo`, and cmd treats ^ as an escape character - so every ^ in the
REM  generated script vanished on the way to disk, taking both of the helper's regex anchors
REM  with it. Then it registered the task with schtasks.exe, which on 2026-09-20 failed while
REM  running as administrator and left nothing behind but a message in a window that closes.
REM
REM  All it does now is hand over to install_session_keeper.ps1, which registers the task
REM  through PowerShell (real error messages), then RUNS it and reads its log to prove it
REM  works. That script elevates itself, so this does not need to.
REM
REM  Undo:  schtasks /delete /tn "Cheezeyverse session keeper" /f     (as administrator)
REM ===================================================================================
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_session_keeper.ps1" -Pause
