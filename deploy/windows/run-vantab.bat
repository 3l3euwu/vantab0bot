@echo off
rem ───────────────────────────────────────────────────────────────────────────
rem  Vantab · keep the bot running on Windows.
rem  Starts bot.py, logs everything to logs\bot.log, and restarts it 10 seconds
rem  after any crash. Close the console window (or run "manage-vantab.ps1 -Stop")
rem  to shut it down.
rem ───────────────────────────────────────────────────────────────────────────
setlocal
title Vantab

rem Repository root = two levels above this script (deploy\windows\..\..)
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%" || exit /b 1

if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
set "LOG=%ROOT%\logs\bot.log"

rem Rotate the log once it passes ~5 MB so it never eats the disk.
for %%F in ("%LOG%") do if %%~zF GEQ 5000000 move /y "%LOG%" "%ROOT%\logs\bot.old.log" >nul 2>&1

set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

:loop
echo [%DATE% %TIME%] starting Vantab >> "%LOG%"
"%PY%" "%ROOT%\bot.py" >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
echo [%DATE% %TIME%] Vantab exited with code %CODE%, restarting in 10s >> "%LOG%"

rem ping is used instead of timeout: it keeps working with no console input.
ping -n 11 127.0.0.1 >nul
goto loop
