# Fireball LLM Single Document & High-Risk Island Reviewer (PowerShell)
# Reviews single document section-by-section and related high-risk keyword islands.
param(
    [string]$file = "",
    [switch]$all,
    [int]$riskThreshold = 0,
    [string]$check = "",
    [switch]$listChecks,
    [switch]$dryRun,
    [string]$backend = "",
    [string]$model = "",
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Single Document & High-Risk Island Review (LLM)

Usage:
  powershell tools/llm-single-review.ps1 [OPTIONS]

Options:
  -file <path>        Path to markdown document to review.
  -all                Review all documents in the project.
  -riskThreshold <N>  Override high-risk score threshold (default from config).
  -check <id>         Run only a specific check ID.
  -listChecks         List all available single review checks and exit.
  -dryRun             Display prompt without calling LLM backend.
  -backend <name>     LLM backend override (openrouter, sakura, ollama, mock).
  -model <name>       LLM model name override.
  -config <path>      Path to configuration file (default: spec-integrator.yaml).
  -h, -help           Show this help message.
"@
    exit 0
}

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$cmdArgs = @("run", "--system-certs", "--project", "tools/spec-integrator",
             "python", "-m", "spec_integrator.cli", "llm-single-review",
             "--config", $config)
if ($file) { $cmdArgs += @("--file", $file) }
if ($all) { $cmdArgs += "--all" }
if ($riskThreshold -gt 0) { $cmdArgs += @("--risk-threshold", "$riskThreshold") }
if ($check) { $cmdArgs += @("--check", $check) }
if ($listChecks) { $cmdArgs += "--list-checks" }
if ($dryRun) { $cmdArgs += "--dry-run" }
if ($backend) { $cmdArgs += @("--backend", $backend) }
if ($model) { $cmdArgs += @("--model", $model) }

& uv @cmdArgs
exit $LASTEXITCODE
