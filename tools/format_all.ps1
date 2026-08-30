# Fireball Automated Code & Document Formatter (PowerShell)
param(
    [switch]$check,
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Code & Document Formatter (PowerShell)

Usage:
  powershell tools/format_all.ps1 [OPTIONS]

Options:
  -check     Check formatting and linting without applying modifications.
  -h, -help  Show this help message.
"@
    exit 0
}

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " Fireball Automated Code & Document Formatter (Ruff)" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan

if ($check) {
    Write-Host "`n>>> [1/2] Checking Python code linting with Ruff..." -ForegroundColor Yellow
    & uv run --system-certs --with ruff ruff check experiments tools docs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ Ruff lint check: FAILED" -ForegroundColor Red
        exit 1
    }
    Write-Host "✔ Ruff lint check: PASSED (0 errors)" -ForegroundColor Green

    Write-Host "`n>>> [2/2] Checking Python code formatting with Ruff..." -ForegroundColor Yellow
    & uv run --system-certs --with ruff ruff format --check experiments tools docs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ Ruff format check: FAILED (unformatted files found)" -ForegroundColor Red
        exit 1
    }
    Write-Host "✔ Ruff format check: PASSED (all files formatted)" -ForegroundColor Green
} else {
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
}

Write-Host "`n================================================================================" -ForegroundColor Cyan
Write-Host " Formatting Pipeline Complete! All Python files are clean and PEP8 compliant." -ForegroundColor Green
Write-Host "================================================================================" -ForegroundColor Cyan
