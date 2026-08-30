# Fireball Document Verification Pipeline (PowerShell Runner)
#
# Phase ordering matters. `llm-assess` decides WHAT must be verified and
# `llm-judge` performs the semantic audit; `check` is the gate that consumes both verdicts
# and is the only authoritative result. Running `check` first — as this script
# used to — meant the risk assessment could demand verification that the gate
# had already declared unnecessary.
#
# The only knob this script exposes is the verification level. Backend, model,
# component, and other fine-tuning belong to `spec-integrator` itself — invoke
# it directly (see tools/spec-integrator/README.md) when you need that control.
param(
    [string]$level = "1",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Document Quality & Verification Pipeline (spec-integrator)

Usage:
  powershell tools/run_all_tests.ps1 [-level <1|2|3|sync>]

Levels:
  1 (default)  Local static gates only. Free, ~5-10s. No LLM calls.
               Phase 0 (lint/fmt) + Phase 3 (concept/bench/semantic) + Phase 4 (check).
               Reuses the stored risk assessment / judge report if present.
  2            Milestone audit. Costs a cloud LLM call, ~30s-1min.
               Level 1 + llm-assess + llm-judge (semantic audit + Design -> Test Spec
               -> Test Code consistency) + the pysim test suite.
  3            Release-gate audit. Costs cloud LLM calls, full coverage, slowest.
               Level 2 with exhaustive assessment/judge coverage across every
               keyword and component, plus a --clean scan.
  sync         Record the current spec state as the propagation baseline, then exit.
               Not a verification level - run this after a spec edit, before Level 1.

  -h, -help    Show this help

Backend, model, tier, and component selection are not exposed here - they are
the same for every level (spec-integrator.yaml's llm_judge.default_backend).
For anything more specific, call spec-integrator directly, e.g.:
  uv run --system-certs --project tools/spec-integrator python -m spec_integrator.cli llm-judge --component jit_compiler
"@
    exit 0
}

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

if ($level -notin @("1", "2", "3", "sync")) {
    Write-Host "✖ Invalid -level '$level'. Use 1, 2, 3, or sync." -ForegroundColor Red
    exit 1
}

$reportsDir = Join-Path $repoRoot "reports"
if (-not (Test-Path $reportsDir)) {
    New-Item -ItemType Directory -Path $reportsDir | Out-Null
}

$specInt = @("run", "--system-certs", "--project", "tools/spec-integrator",
             "python", "-m", "spec_integrator.cli")

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " Fireball Document Verification Pipeline [spec-integrator] - Level $level" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Phase 0: Python Code Lint & Formatting Gate — runs at every level.
# ---------------------------------------------------------------------------
Write-Host "`n>>> [Phase 0] Python Code Linter & Formatter Verification (Ruff)..." -ForegroundColor Yellow
& uv run --system-certs --with ruff ruff check experiments tools docs
if ($LASTEXITCODE -ne 0) {
    Write-Host "✖ Python Lint Check: FAILED — run 'powershell tools/format_all.ps1' to auto-fix." -ForegroundColor Red
    exit 1
}
& uv run --system-certs --with ruff ruff format --check experiments tools docs
if ($LASTEXITCODE -ne 0) {
    Write-Host "✖ Python Format Check: FAILED — run 'powershell tools/format_all.ps1' to auto-format." -ForegroundColor Red
    exit 1
}
Write-Host "✔ Python Linter & Formatter: All checks passed (0 errors)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Accept the current specification state as the propagation baseline.
# Deliberately not part of the pipeline: doing it automatically would erase the
# very record that reveals an edit which never reached its dependants.
# ---------------------------------------------------------------------------
if ($level -eq "sync") {
    Write-Host "`n>>> Recording consistency baseline..." -ForegroundColor Yellow
    & uv @($specInt + @("sync", "--config", "spec-integrator.yaml"))
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ Baseline sync: FAILED" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Commit spec-consistency.lock together with the spec changes." -ForegroundColor DarkGray
    exit 0
}

$runLLM = $level -in @("2", "3")
$exhaustive = $level -eq "3"
$maxKeywords = if ($exhaustive) { 0 } else { 15 }
$maxSubgraphs = if ($exhaustive) { 0 } else { 10 }
$maxDocuments = if ($exhaustive) { 0 } else { 15 }

# ---------------------------------------------------------------------------
# Phase 1: Risk Assessment — establishes the verification obligations
# ---------------------------------------------------------------------------
if ($runLLM) {
    Write-Host "`n>>> [Phase 1/4] Risk Assessment (deciding what must be verified)..." -ForegroundColor Yellow
    $assessArgs = $specInt + @("llm-assess", "--config", "spec-integrator.yaml",
                               "--max-keywords", "$maxKeywords")
    if ($exhaustive) { $assessArgs += "--exhaustive" }

    & uv @assessArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ Risk Assessment: FAILED (incomplete coverage leaves obligations unknown)" -ForegroundColor Red
        Write-Host "  Use -level 3 for exhaustive (unlimited-section) coverage." -ForegroundColor DarkGray
        exit 1
    }
    Write-Host "✔ Risk Assessment: obligations recorded in the cache DB" -ForegroundColor Green
} else {
    Write-Host "`n>>> [Phase 1/4] Skipping Risk Assessment (-level 2 or 3 to run it)" -ForegroundColor DarkGray
    Write-Host "    Reusing whatever assessment is already in the cache DB, if any. The gate" -ForegroundColor DarkGray
    Write-Host "    will reject it if the docs have changed, and fail if none exists." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Phase 2: LLM Semantic Audit — subgraph consistency, whole-document
# self-consistency, AND the Design -> Test Spec -> Test Code traceability
# chain always run together in one pass.
# ---------------------------------------------------------------------------
if ($runLLM) {
    Write-Host "`n>>> [Phase 2/4] LLM as a Judge (subgraph + whole-document + Design -> Test Spec -> Test Code consistency)..." -ForegroundColor Yellow
    $judgeArgs = $specInt + @("llm-judge", "--config", "spec-integrator.yaml",
                              "--max-subgraphs", "$maxSubgraphs",
                              "--max-documents", "$maxDocuments")
    if ($exhaustive) { $judgeArgs += "--exhaustive" }

    & uv @judgeArgs
    $judgeExit = $LASTEXITCODE
    if ($judgeExit -ne 0) {
        # A FAIL verdict is data for the gate, not a reason to abort the pipeline.
        Write-Host "! LLM as a Judge reported findings — see reports/doc_report.md § LLM Judge Verdicts / § Whole-Document LLM Judge Verdicts / § Test Chain Verdicts" -ForegroundColor DarkYellow
    } else {
        Write-Host "✔ LLM as a Judge: no semantic failures" -ForegroundColor Green
    }
} else {
    Write-Host "`n>>> [Phase 2/4] Skipping LLM as a Judge (-level 2 or 3 to run it)" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Phase 3: Concept Code Verification — the reference implementations under
# docs/**/concepts/*_concept.py are not test_*.py, so pytest silently collects
# zero tests from them and no other phase ever imports or executes them. This
# is the only thing that actually runs each one and checks it still works.
# ---------------------------------------------------------------------------
Write-Host "`n>>> [Phase 3/4] Concept Code Verification (running docs/**/concepts/*_concept.py)..." -ForegroundColor Yellow
$conceptFiles = Get-ChildItem -Path "docs" -Filter "*_concept.py" -Recurse
$conceptFailed = $false
foreach ($f in $conceptFiles) {
    $relPath = Resolve-Path -Relative $f.FullName
    & uv run --system-certs --project tools/spec-integrator python $f.FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ $relPath FAILED" -ForegroundColor Red
        $conceptFailed = $true
    } else {
        Write-Host "✔ $relPath" -ForegroundColor Green
    }
}
if ($conceptFailed) {
    Write-Host "✖ Concept Code Verification: FAILED" -ForegroundColor Red
} else {
    Write-Host "✔ Concept Code Verification: $($conceptFiles.Count) file(s) passed" -ForegroundColor Green
}

# Benchmarks: empirical backing for keywords whose requirement_list.md verification
# method is "ベンチマーク" (Benchmark), tagged {VERIFY_BENCHMARK} and checked for
# existence by the Evidence gate below. Running them here (not just checking they
# exist) catches a benchmark that has silently started failing its own assertions.
$benchFiles = Get-ChildItem -Path "docs" -Filter "*_bench.py" -Recurse
foreach ($f in $benchFiles) {
    $relPath = Resolve-Path -Relative $f.FullName
    & uv run --system-certs --project tools/spec-integrator python $f.FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ $relPath FAILED" -ForegroundColor Red
        $conceptFailed = $true
    } else {
        Write-Host "✔ $relPath" -ForegroundColor Green
    }
}
if ($benchFiles.Count -gt 0) {
    Write-Host "✔ Benchmarks: $($benchFiles.Count) file(s) ran" -ForegroundColor Green
}

# Dynamic semantic check: actually executes the JIT stencil catalog's machine code
# on a real ARMv8-M Thumb emulator (unicorn) and checks the resulting register state
# against the WASM-specified result, instead of only comparing bytes to a second
# hand-written copy. Needs the `unicorn` package, which is not a spec-integrator
# dependency, so it is invoked separately via `--with`.
foreach ($semVerifier in @(
    "docs/components/tier3_jit/concepts/thumb2_stencil_semantic_verifier.py",
    "docs/components/tier3_jit/concepts/jit_trace_execution_verifier.py"
)) {
    if (Test-Path $semVerifier) {
        & uv run --system-certs --project tools/spec-integrator --with unicorn python $semVerifier
        if ($LASTEXITCODE -ne 0) {
            Write-Host "✖ $semVerifier FAILED" -ForegroundColor Red
            $conceptFailed = $true
        } else {
            Write-Host "✔ $semVerifier" -ForegroundColor Green
        }
    }
}

# ---------------------------------------------------------------------------
# Python Simulator (pysim) Invariant & Integration Scenarios — free and local,
# but slow enough (~15-20s) to reserve for Level 2+.
# ---------------------------------------------------------------------------
if ($runLLM) {
    Write-Host "`n>>> [pysim] Python Simulator Unit & Scenario Test Suite..." -ForegroundColor Yellow
    Write-Host "  -> Running experiments/pysim/tests/run_all.py..." -ForegroundColor DarkGray
    & uv run --system-certs --project tools/spec-integrator --with wasmtime python experiments/pysim/tests/run_all.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ pysim Unit Tests: FAILED" -ForegroundColor Red
        $conceptFailed = $true
    }
    Write-Host "  -> Running experiments/pysim/scenarios/run_all.py..." -ForegroundColor DarkGray
    & uv run --system-certs --project tools/spec-integrator --with wasmtime python experiments/pysim/scenarios/run_all.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✖ pysim Scenario Tests: FAILED" -ForegroundColor Red
        $conceptFailed = $true
    }
}

# ---------------------------------------------------------------------------
# Phase 4: Quality Gates — the authoritative verdict
# ---------------------------------------------------------------------------
Write-Host "`n>>> [Phase 4/4] Quality Gates (Format / Traceability / Hierarchy / Formal / WIT / Evidence / Obligation / Consistency)..." -ForegroundColor Yellow
$checkArgs = $specInt + @("check", "--config", "spec-integrator.yaml",
                          "--report", "reports/doc_report.md")
if ($exhaustive) { $checkArgs += "--clean" }

& uv @checkArgs
$checkExit = $LASTEXITCODE

Write-Host "`n================================================================================" -ForegroundColor Cyan
if ($conceptFailed -or $checkExit -ne 0) {
    Write-Host " Verification Pipeline Summary: FAILED" -ForegroundColor Red
    if ($conceptFailed) { Write-Host " Concept code verification failed — see output above." -ForegroundColor Cyan }
    if ($checkExit -ne 0) { Write-Host " See reports/doc_report.md for the full list of violations." -ForegroundColor Cyan }
    Write-Host "================================================================================" -ForegroundColor Cyan
    exit 1
}

Write-Host " Verification Pipeline Summary: PASSED" -ForegroundColor Green
Write-Host " Reports saved to: reports/" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
exit 0
