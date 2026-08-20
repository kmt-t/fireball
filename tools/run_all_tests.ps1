# Fireball Document Verification Pipeline (PowerShell Runner)
param(
    [switch]$llm,
    [string]$backend = "sakura",
    [string]$model = "",
    [int]$maxSubgraphs = 10,
    [switch]$clean
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$cleanFlag = if ($clean) { "--clean" } else { "" }

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " Fireball Document Verification Pipeline [spec-integrator]" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan

# Phase 1: Check
Write-Host ">>> [Phase 1/2] Running Static & Formal Model Verification..." -ForegroundColor Yellow
$checkArgs = @("run", "--system-certs", "--project", "tools/spec-integrator", "python", "-m", "spec_integrator.cli", "check", "--config", "spec-integrator.yaml", "--report", "doc_report.md", "--graph-json", "doc_graph.json")
if ($clean) { $checkArgs += "--clean" }

& uv @checkArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "✖ Quality Gates or Formal Verification: FAILED" -ForegroundColor Red
    exit 1
}
Write-Host "✔ Quality Gates & Formal Verification: PASSED" -ForegroundColor Green

# Phase 2: Judge
if ($llm) {
    Write-Host "`n>>> [Phase 2/2] Running LLM as a Judge Semantic Audits..." -ForegroundColor Yellow
    $judgeArgs = @("run", "--system-certs", "--project", "tools/spec-integrator", "python", "-m", "spec_integrator.cli", "judge", "--config", "spec-integrator.yaml", "--backend", $backend, "--max-subgraphs", "$maxSubgraphs", "-o", "doc_judge_report.json")
    if ($model) { $judgeArgs += @("--model", $model) }
    & uv @judgeArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ LLM as a Judge: FAILED" -ForegroundColor Red
        exit 1
    }
    Write-Host "✔ LLM as a Judge: PASSED" -ForegroundColor Green
} else {
    Write-Host "`n>>> [Phase 2/2] Skipping LLM as a Judge (Use -llm to enable)" -ForegroundColor DarkGray
}

Write-Host "`n================================================================================" -ForegroundColor Cyan
Write-Host " Verification Pipeline Summary: SUCCESS" -ForegroundColor Green
Write-Host " Report saved to: doc_report.md" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
exit 0
