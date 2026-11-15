@echo off
REM ============================================================
REM  SwarmLabs unattended nightly ops - install scheduled tasks
REM  Night runs: deploy refresh -> full verify -> morning brief
REM ============================================================
setlocal
set ROOT=C:\Users\xing\Desktop\swarmlabs
set NODE=node
set TASK_NIGHT=SwarmLabs_NightOps
set TASK_DAY=SwarmLabs_DayVerify

REM idempotent delete old tasks
schtasks /Delete /TN %TASK_NIGHT% /F >nul 2>&1
schtasks /Delete /TN %TASK_DAY% /F >nul 2>&1
schtasks /Delete /TN SwarmLabs_DailyMonitor /F >nul 2>&1
schtasks /Delete /TN SwarmLabs_NightlyDeploy /F >nul 2>&1

REM main: every 03:00 deploy + full verify + morning brief
schtasks /Create /TN %TASK_NIGHT% /TR "\"%NODE%\" \"%ROOT%\scripts\daily_ops.js\" --deploy" /SC DAILY /ST 03:00 /F

REM auxiliary: every 12:30 verify only + brief
schtasks /Create /TN %TASK_DAY% /TR "\"%NODE%\" \"%ROOT%\scripts\daily_ops.js\"" /SC DAILY /ST 12:30 /F

echo.
echo [OK] installed:
echo   %TASK_NIGHT%  every 03:00 - auto deploy + verify + brief
echo   %TASK_DAY%    every 12:30 - verify + brief
echo.
echo Morning brief: reports\briefs\morning-brief-<date>.md
echo.
pause