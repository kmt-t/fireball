# Fireball Document Verification Pipeline (PowerShell Runner)
#
# Phase ordering matters. `assess` decides WHAT must be verified and `judge`
# performs the semantic audit; `check` is the gate that consumes both verdicts
# and is the only authoritative result. Running `check` first — as this script
# used to — meant the risk assessment could demand verification that the gate
# had already declared unnecessary.
param(
    [switch]$llm,
    [switch]$assess,
    [switch]$full,
    [string]$backend = "sakura",
    [string]$model = "",
    [int]$maxSubgraphs = 10,
    [int]$maxSections = 15,
    [switch]$noStrict,
    [switch]$sync,
    [switch]$clean
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$reportsDir = Join-Path $repoRoot "reports"
if (-not (Test-Path $reportsDir)) {
    New-Item -ItemType Directory -Path $reportsDir | Out-Null
}

if ($full) {
    $assess = $true
    $llm = $true
    if (-not $PSBoundParameters.ContainsKey('maxSections')) { $maxSections = 1000 }
    if (-not $PSBoundParameters.ContainsKey('maxSubgraphs')) { $maxSubgraphs = 200 }
}

$specInt = @("run", "--system-certs", "--project", "tools/spec-integrator",
             "python", "-m", "spec_integrator.cli")

$riskReport = "reports/doc_risk_report.json"
$judgeReport = "reports/doc_judge_report.json"

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " Fireball Document Verification Pipeline [spec-integrator]" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Accept the current specification state as the propagation baseline.
# Deliberately not part of the pipeline: doing it automatically would erase the
# very record that reveals an edit which never reached its dependants.
# ---------------------------------------------------------------------------
if ($sync) {
    Write-Host "`n>>> Recording consistency baseline..." -ForegroundColor Yellow
    & uv @($specInt + @("sync", "--config", "spec-integrator.yaml"))
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ Baseline sync: FAILED" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Commit spec-consistency.lock together with the spec changes." -ForegroundColor DarkGray
    exit 0
}

# ---------------------------------------------------------------------------
# Phase 1: Risk Assessment — establishes the verification obligations
# ---------------------------------------------------------------------------
if ($assess) {
    Write-Host "`n>>> [Phase 1/3] Risk Assessment (deciding what must be verified)..." -ForegroundColor Yellow
    $assessArgs = $specInt + @("assess", "--config", "spec-integrator.yaml",
                               "--backend", $backend, "--max-sections", "$maxSections",
                               "-o", $riskReport, "-r", "reports/doc_risk_report.md")
    if ($model) { $assessArgs += @("--model", $model) }
    if ($noStrict) { $assessArgs += "--no-strict" }

    & uv @assessArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ Risk Assessment: FAILED (incomplete coverage leaves obligations unknown)" -ForegroundColor Red
        Write-Host "  Raise -maxSections, or pass -noStrict to accept a partial assessment." -ForegroundColor DarkGray
        exit 1
    }
    Write-Host "✔ Risk Assessment: obligations recorded in $riskReport" -ForegroundColor Green
} else {
    Write-Host "`n>>> [Phase 1/3] Skipping Risk Assessment (-assess to run it)" -ForegroundColor DarkGray
    if (Test-Path $riskReport) {
        Write-Host "    Reusing the stored assessment. The gate will reject it if the docs have changed." -ForegroundColor DarkGray
    } else {
        Write-Host "    No stored assessment exists — the Obligation Gate will fail." -ForegroundColor DarkYellow
    }
}

# ---------------------------------------------------------------------------
# Phase 2: LLM Semantic Audit
# ---------------------------------------------------------------------------
if ($llm) {
    Write-Host "`n>>> [Phase 2/3] LLM as a Judge (semantic audit)..." -ForegroundColor Yellow
    $judgeArgs = $specInt + @("judge", "--config", "spec-integrator.yaml",
                              "--backend", $backend, "--max-subgraphs", "$maxSubgraphs",
                              "-o", $judgeReport)
    if ($model) { $judgeArgs += @("--model", $model) }

    & uv @judgeArgs
    $judgeExit = $LASTEXITCODE
    if ($judgeExit -ne 0) {
        # A FAIL verdict is data for the gate, not a reason to abort the pipeline.
        Write-Host "! LLM as a Judge reported findings — see $judgeReport" -ForegroundColor DarkYellow
    } else {
        Write-Host "✔ LLM as a Judge: no semantic failures" -ForegroundColor Green
    }
} else {
    Write-Host "`n>>> [Phase 2/3] Skipping LLM as a Judge (-llm to run it)" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Phase 3: Quality Gates — the authoritative verdict
# ---------------------------------------------------------------------------
Write-Host "`n>>> [Phase 3/3] Quality Gates (Format / Traceability / Hierarchy / Formal / WIT / Evidence / Obligation / Consistency)..." -ForegroundColor Yellow
$checkArgs = $specInt + @("check", "--config", "spec-integrator.yaml",
                          "--report", "reports/doc_report.md",
                          "--graph-json", "reports/doc_graph.json")
if ($clean) { $checkArgs += "--clean" }

& uv @checkArgs
$checkExit = $LASTEXITCODE

Write-Host "`n================================================================================" -ForegroundColor Cyan
if ($checkExit -ne 0) {
    Write-Host " Verification Pipeline Summary: FAILED" -ForegroundColor Red
    Write-Host " See reports/doc_report.md for the full list of violations." -ForegroundColor Cyan
    Write-Host "================================================================================" -ForegroundColor Cyan
    exit 1
}

Write-Host " Verification Pipeline Summary: PASSED" -ForegroundColor Green
Write-Host " Reports saved to: reports/" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
exit 0
