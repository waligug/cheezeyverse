@echo off
REM ===================================================================================
REM  Hand this RDP session back to the console, so the desktop keeps rendering after you
REM  disconnect.
REM
REM  WHY THIS EXISTS
REM  The commissioner drives FBPB3 with real mouse clicks, because it is a VB6 app with no
REM  API and its owner-drawn controls ignore posted messages. Real clicks need a desktop
REM  that is actually being drawn. When you close an RDP window, Windows LOCKS the session:
REM  it still exists, the processes keep running, but nothing renders and nothing can be
REM  clicked. A sim running at that moment fails partway through, which is the worst time.
REM
REM  tscon moves your session to the console instead. The console session never locks on
REM  disconnect, and it renders perfectly well with no monitor plugged in - Windows keeps a
REM  virtual display for it either way.
REM
REM  WHAT HAPPENS WHEN YOU RUN IT
REM  Your remote desktop window will disconnect IMMEDIATELY. That is the whole point, not a
REM  crash. Everything on the server keeps running, now on a desktop that stays clickable.
REM  Reconnect by RDP whenever you like; run this again before you disconnect next time.
REM
REM  MUST BE RUN AS ADMINISTRATOR. tscon needs it.
REM ===================================================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   This has to run as Administrator - tscon will not work otherwise.
    echo   Right-click it and choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo.
echo   Handing session back to the console. Your RDP window will disconnect now.
echo   The server keeps running and the desktop stays clickable.
echo.

for /f "skip=1 tokens=3" %%s in ('query user "%USERNAME%" 2^>nul') do (
    if not "%%s"=="" (
        "%windir%\System32\tscon.exe" %%s /dest:console
        goto :done
    )
)

echo   Could not work out which session to move. Run "query user" and then:
echo       tscon ^<session id^> /dest:console
pause

:done
exit /b 0
