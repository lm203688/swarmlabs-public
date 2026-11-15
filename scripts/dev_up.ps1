# SwarmLabs dev environment bootstrapper (PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\dev_up.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> SwarmLabs dev environment setup" -ForegroundColor Cyan
Write-Host "    repo root : $RepoRoot"
Write-Host "    python    : $(python --version)"
Write-Host ""

# 1. Install in editable mode
if (-not $env:SKIP_INSTALL) {
    Write-Host "==> pip install -e .[dev]"
    python -m pip install -e ".[dev]" 2>&1 | Select-Object -Last 5
} else {
    Write-Host "==> skipping install (SKIP_INSTALL=1)"
}

Write-Host ""
Write-Host "==> doctor"
python -m swarmlabs doctor

Write-Host ""
Write-Host "==> check battery"
& python -m swarmlabs check
Write-Host "    (non-zero exit is expected if engine was just modified)"

Write-Host ""
Write-Host "==> end-to-end demo"
python -m swarmlabs demo

Write-Host ""
Write-Host "==> done. Try:"
Write-Host "    swarmlabs scenarios          # list 82 scenarios"
Write-Host "    swarmlabs gates              # list 11 registered gates"
Write-Host "    swarmlabs anchors            # list 19 wet-lab anchors"
Write-Host "    swarmlabs serve              # start the v3 API on :8787"
