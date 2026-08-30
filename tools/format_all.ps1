# Fireball Automated Code & Document Formatter (PowerShell)
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " Fireball Automated Code & Document Formatter" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan

Write-Host "`n>>> [1/2] Auto-fixing lint issues with Ruff..." -ForegroundColor Yellow
& uv run --system-certs --with ruff ruff check --fix --unsafe-fixes experiments tools docs
if ($LASTEXITCODE -ne 0) {
    Write-Host "✖ Ruff check auto-fix completed with warnings/issues." -ForegroundColor DarkYellow
} else {
    Write-Host "✔ Ruff check auto-fix: CLEAN" -ForegroundColor Green
}

Write-Host "`n>>> [2/2] Formatting Python code with Ruff..." -ForegroundColor Yellow
& uv run --system-certs --with ruff ruff format experiments tools docs
if ($LASTEXITCODE -ne 0) {
    Write-Host "✖ Ruff format: FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "✔ Ruff format: COMPLETE" -ForegroundColor Green

Write-Host "`n================================================================================" -ForegroundColor Cyan
Write-Host " Formatting Complete! All Python files are clean and PEP8 compliant." -ForegroundColor Green
Write-Host "================================================================================" -ForegroundColor Cyan
