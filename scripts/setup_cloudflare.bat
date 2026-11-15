@echo off
REM ============================================================
REM  SwarmLabs 一键式无人值守运维开启器（v2 融合版）
REM  最省事：只需运行一次（双击本文件）
REM  它会：检测登录 -> 缺失则引导登录 -> 首次部署 -> 注册每日任务
REM  之后每日自动验证+部署+监控，无需你再参与
REM ============================================================
setlocal enabledelayedexpansion
set ROOT=C:\Users\xing\Desktop\swarmlabs
set PYTHON=python
set CFTOKEN=%USERPROFILE%\.wrangler\config\default.toml

echo.
echo  ==== SwarmLabs 无人值守自动化 · 一次性开启（v2 融合版）====
echo.

REM ---------- 步骤1: 检测/引导 Cloudflare 登录 ----------
if exist "%CFTOKEN%" (
  echo  [OK] 检测到 Cloudflare 登录态，无需重新登录。
) else (
  echo  [!] 未检测到 Cloudflare 登录态。
  echo      即将打开浏览器进行一次性授权(wrangler login)。
  echo      请在弹出的窗口中点击 Allow / 授权 即可。完成后本脚本自动继续。
  echo.
  cd /d "%ROOT%\frontend_fixed"
  call npx wrangler login
  if not exist "%CFTOKEN%" (
    echo.
    echo  [ERR] 登录似乎未完成。请重试脚本，或在浏览器中完成授权。
    pause
    exit /b 1
  )
  echo  [OK] Cloudflare 授权完成，已保存登录态。
)

REM ---------- 步骤2: 首次部署到 Cloudflare Pages ----------
echo.
echo  进行首次自动部署验证...
cd /d "%ROOT%\frontend_fixed"
call npx wrangler pages deploy . --project-name swarmlabs
echo.
echo  [DONE] 部署完成。

REM ---------- 步骤3: 注册 Windows 每日计划任务（v2 统一入口）----------
echo.
echo  注册每日自动任务（统一入口 unified_ops.py）...
schtasks /Delete /TN SwarmLabs_DayVerify /F >nul 2>&1
schtasks /Delete /TN SwarmLabs_NightOps /F >nul 2>&1
schtasks /Delete /TN SwarmLabs_DailyMonitor /F >nul 2>&1
schtasks /Delete /TN SwarmLabs_NightlyDeploy /F >nul 2>&1
schtasks /Create /TN SwarmLabs_DayVerify  /TR "\"%PYTHON%\" \"%ROOT%\scripts\unified_ops.py\""       /SC DAILY /ST 12:30 /F
schtasks /Create /TN SwarmLabs_NightOps   /TR "\"%PYTHON%\" \"%ROOT%\scripts\unified_ops.py\" --deploy" /SC DAILY /ST 03:00 /F

echo.
echo  =================================================
echo   全部完成！（v2 融合版）
echo   - 每日 12:30 完整闭环（验证+增长+竞品+AgentLoop+报告）
echo   - 每日 03:00 验证+Cloudflare 部署
echo   - 统一入口: scripts/unified_ops.py
echo   - 日志: reports/daily-ops-log/daily-ops-YYYY-MM-DD.json
echo   从今以后 SwarmLabs 运营自动化，无需你手动参与。
echo  =================================================
pause
