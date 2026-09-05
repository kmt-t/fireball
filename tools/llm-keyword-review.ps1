# Fireball LLM High-Risk Keyword Island Reviewer (PowerShell)
# Reviews connected document islands associated with high-risk keywords.
param(
    [string]$keyword = "",
    [int]$minRisk = 0,
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
Fireball High-Risk Keyword Island Review (LLM)

Usage:
  powershell tools/llm-keyword-review.ps1 [OPTIONS]

Options:
  -keyword <name>     Target a specific keyword's connected island.
  -minRisk <N>        Minimum risk score filter for keywords (default from config).
  -check <id>         Run only a specific check ID.
  -listChecks         List all available island review checks and exit.
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
             "python", "-m", "spec_integrator.cli", "llm-keyword-review",
             "--config", $config)
if ($keyword) { $cmdArgs += @("--keyword", $keyword) }
if ($minRisk -gt 0) { $cmdArgs += @("--min-risk", "$minRisk") }
if ($check) { $cmdArgs += @("--check", $check) }
if ($listChecks) { $cmdArgs += "--list-checks" }
if ($dryRun) { $cmdArgs += "--dry-run" }
if ($backend) { $cmdArgs += @("--backend", $backend) }
if ($model) { $cmdArgs += @("--model", $model) }

& uv @cmdArgs
exit $LASTEXITCODE
