@echo off
setlocal enabledelayedexpansion
REM ===================================================================================
REM  Cheezeyverse - set this machine up to run the universe.
REM
REM  Run this ON THE SERVER, from the folder that was copied across (the one holding
REM  leaguedata\ and env\). It does every step that needs to happen locally:
REM
REM     1. checks git, python and FBPB3 are present
REM     2. clones the code to C:\claude\hoops-universe
REM     3. installs the Python packages
REM     4. puts the three saves where FBPB3 looks for them
REM     5. puts .env where the commissioner looks for it
REM     6. runs the health check
REM
REM  It is safe to run twice. Nothing here overwrites a save that is already in place
REM  without telling you.
REM ===================================================================================

set "BUNDLE=%~dp0"
set "REPO=C:\claude\hoops-universe"
set "GAMEDATA=C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3"

echo.
echo  ================================================================
echo   CHEEZEYVERSE SERVER SETUP
echo  ================================================================
echo   bundle : %BUNDLE%
echo   repo   : %REPO%
echo.

REM ---------------------------------------------------------------- prerequisites
echo  [1/6] Checking what is installed...

where git >nul 2>&1
if errorlevel 1 (
    echo        MISSING: git.  Install it from https://git-scm.com/download/win
    goto :fail
)
echo        git      ok

where python >nul 2>&1
if errorlevel 1 (
    echo        MISSING: python.  Install 3.11 from python.org and tick "Add to PATH".
    goto :fail
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo        python   ok  ^(%%v^)

if not exist "C:\Program Files (x86)\GDS\Fast Break Pro Basketball 3\FBPB3.exe" (
    echo        MISSING: FBPB3 is not at its default location.
    echo        Expected: C:\Program Files ^(x86^)\GDS\Fast Break Pro Basketball 3\FBPB3.exe
    goto :fail
)
echo        FBPB3    ok

tasklist /FI "IMAGENAME eq FBPB3.exe" 2>nul | find /I "FBPB3.exe" >nul
if not errorlevel 1 (
    echo.
    echo        FBPB3 IS RUNNING. Close it first - it writes league.dat when it exits
    echo        and would overwrite the saves this script is about to put in place.
    goto :fail
)

REM ---------------------------------------------------------------- the code
echo.
echo  [2/6] Getting the code...
if exist "%REPO%\.git" (
    echo        already cloned; pulling the latest
    pushd "%REPO%"
    git pull --ff-only
    popd
) else (
    git clone https://github.com/waligug/cheezeyverse.git "%REPO%"
    if errorlevel 1 goto :fail
)

REM ---------------------------------------------------------------- packages
echo.
echo  [3/6] Installing Python packages...
python -m pip install --quiet --disable-pip-version-check -r "%REPO%\requirements.txt"
if errorlevel 1 (
    echo        pip failed. Try:  python -m pip install -r "%REPO%\requirements.txt"
    goto :fail
)
echo        done

REM ---------------------------------------------------------------- the saves
echo.
echo  [4/6] Putting the saves where FBPB3 looks for them...
if not exist "%GAMEDATA%" mkdir "%GAMEDATA%"

if exist "%GAMEDATA%\leaguedata\CV_Prep\league.dat" (
    echo.
    echo        There are already Cheezeyverse saves on this machine.
    echo        Overwriting them would discard whatever has been simmed here.
    echo.
    choice /C YN /M "        Overwrite them with the ones in this bundle"
    if errorlevel 2 (
        echo        keeping what is already there
        goto :skipsaves
    )
)
robocopy "%BUNDLE%leaguedata" "%GAMEDATA%\leaguedata" /E /NFL /NDL /NJH /NJS /NP >nul
robocopy "%BUNDLE%LeagueFiles" "%GAMEDATA%\LeagueFiles" /E /NFL /NDL /NJH /NJS /NP >nul
robocopy "%BUNDLE%PlayerFiles" "%GAMEDATA%\PlayerFiles" /E /NFL /NDL /NJH /NJS /NP >nul
echo        copied
:skipsaves

for %%s in (CV_Prep CV_College CV_Pro) do (
    if exist "%GAMEDATA%\leaguedata\%%s\league.dat" (
        echo        %%s ok
    ) else (
        echo        %%s MISSING
    )
)

REM ---------------------------------------------------------------- the key
echo.
echo  [5/6] Putting the Supabase key in place...
if exist "%REPO%\.env" (
    echo        .env already there; leaving it alone
) else (
    if exist "%BUNDLE%env\.env" (
        copy /Y "%BUNDLE%env\.env" "%REPO%\.env" >nul
        echo        copied
    ) else (
        echo        NOT FOUND in the bundle - the commissioner will run offline without it
    )
)

REM ---------------------------------------------------------------- check
echo.
echo  [6/6] Checking this machine can actually run a Sim Week...
echo.
pushd "%REPO%"
python tools\server_doctor.py
set DOCTOR=%errorlevel%
popd

echo.
echo  ================================================================
if %DOCTOR%==0 (
    echo   Setup finished and the machine checks out.
    echo.
    echo   Two things left, both need you:
    echo.
    echo     gh auth login                      so the server can publish the site
    echo     python tools\verify_save.py        confirm the saves arrived intact
    echo.
    echo   Then start it:
    echo     cd %REPO%
    echo     python -m commissioner.app --lan
    echo.
    echo   It prints an address. Open that from your desktop and press Sim Week.
) else (
    echo   The health check found problems - see the FAIL lines above.
    echo   Each one says how to fix it. Re-run this script afterwards.
)
echo  ================================================================
echo.
pause
exit /b %DOCTOR%

:fail
echo.
echo  ================================================================
echo   Stopped. Fix the item above and run this again.
echo  ================================================================
echo.
pause
exit /b 1
