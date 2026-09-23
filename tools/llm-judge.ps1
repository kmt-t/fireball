# Fireball Anchored LLM Semantic Judge Runner (PowerShell)
# Audits all {VERIFY_LLM}-tagged documents (whole-document + cross-document keyword link-pair review)
# and persists anchored verdicts so the Obligation Verifier can discharge OBLIG-JUDGE-* / OBLIG-DOC-JUDGE-*.
param(
    [int]$maxDocuments = 20,
    [int]$maxKeywordGroups = 20,
    [switch]$exhaustive,
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
Fireball Anchored LLM Semantic Judge

Usage:
  powershell tools/llm-judge.ps1 [OPTIONS]

Options:
  -maxDocuments <N>   Max tagged documents to audit in whole-document mode (default: 20, 0 for unlimited).
  -maxKeywordGroups <N> Max keyword groups to audit as link pairs (default: 20, 0 for unlimited).
  -exhaustive         Ignore -maxDocuments/-maxKeywordGroups and audit full coverage.
  -check <id>         Run only a specific check ID.
  -listChecks         List all configured single-section/link-pair review checks and exit.
  -dryRun             Display prompts without calling the LLM backend or persisting results.
  -backend <name>     LLM backend override (jev, openrouter, sakura, ollama, mock).
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
             "python", "-m", "spec_integrator.cli", "llm-judge",
             "--config", $config, "--max-documents", "$maxDocuments",
             "--max-keyword-groups", "$maxKeywordGroups")
if ($exhaustive) { $cmdArgs += "-a" }
if ($check) { $cmdArgs += @("--check", $check) }
if ($listChecks) { $cmdArgs += "--list-checks" }
if ($dryRun) { $cmdArgs += "--dry-run" }
if ($backend) { $cmdArgs += @("--backend", $backend) }
if ($model) { $cmdArgs += @("--model", $model) }

& uv @cmdArgs
exit $LASTEXITCODE
