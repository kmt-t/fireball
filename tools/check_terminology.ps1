# Fireball Terminology & Spelling Variance Checker (PowerShell)
param(
    [switch]$quick,          # Quick mode: static Levenshtein & embeddings only (no LLM judge)
    [int]$maxPairs = 20,     # Max pairs to judge with LLM
    [string]$backend = "",   # LLM backend override (sakura, openrouter, ollama, mock)
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Terminology & Spelling Variance Checker (PowerShell)

Usage:
  powershell tools/check_terminology.ps1 [OPTIONS]

Options:
  -quick              Run fast static check only (TF-IDF + Levenshtein + Embedding cache; skips LLM).
  -maxPairs <N>       Maximum number of candidate pairs to judge via LLM (default: 20, 0 for unlimited).
  -backend <name>     LLM backend override (sakura, openrouter, ollama, mock).
  -h, -help           Show this help message.
"@
    exit 0
}

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " Fireball Terminology & Spelling Variance Checker Pipeline" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan

$specInt = @("run", "--system-certs", "--project", "tools/spec-integrator", "python", "-m", "spec_integrator.cli")

# Step 1: Sync TF-IDF Term Database
Write-Host "`n>>> [1/4] Extracting terminology via TF-IDF (sync)..." -ForegroundColor Yellow
& uv @($specInt + @("sync", "--config", "spec-integrator.yaml"))
if ($LASTEXITCODE -ne 0) {
    Write-Host "✖ Terminology extraction failed." -ForegroundColor Red
    exit 1
}

# Step 2: Index Term Embeddings & Compute Similarities
Write-Host "`n>>> [2/4] Indexing embeddings & computing similarity pairs (term-index)..." -ForegroundColor Yellow
& uv @($specInt + @("term-index", "--config", "spec-integrator.yaml"))
if ($LASTEXITCODE -ne 0) {
    Write-Host "✖ Term indexing failed." -ForegroundColor Red
    exit 1
}

# Step 3: LLM Contextual Variance Judgment
if ($quick) {
    Write-Host "`n>>> [3/4] Skipping LLM contextual judgment (-quick specified)..." -ForegroundColor DarkGray
} else {
    Write-Host "`n>>> [3/4] Running LLM contextual variance judgment (term-judge, max: $maxPairs pairs)..." -ForegroundColor Yellow
    $judgeArgs = @("term-judge", "--config", "spec-integrator.yaml", "--max-pairs", "$maxPairs")
    if ($backend) {
        $judgeArgs += @("--backend", $backend)
    }
    & uv @($specInt + $judgeArgs)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ Term judgment failed." -ForegroundColor Red
        exit 1
    }
}

# Step 4: Consolidated Terminology Report
Write-Host "`n>>> [4/4] Generating consolidated terminology report..." -ForegroundColor Yellow
& uv @($specInt + @("term-report", "--config", "spec-integrator.yaml"))
if ($LASTEXITCODE -ne 0) {
    Write-Host "✖ Report generation failed." -ForegroundColor Red
    exit 1
}

Write-Host "✔ Terminology check complete." -ForegroundColor Green
