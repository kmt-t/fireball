# Fireball Terminology & Spelling Variance Checker (PowerShell)
# Indexes embeddings, links similar terms, judges variance via LLM, and outputs report.
param(
    [switch]$quick,          # Quick mode: static Levenshtein & embeddings only (skips LLM judge)
    [int]$maxPairs = 20,     # Max pairs to judge with LLM
    [float]$threshold = 0.80,# Cosine similarity threshold
    [string]$backend = "",   # LLM backend override (sakura, openrouter, ollama, mock)
    [string]$model = "",     # Model override
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Terminology & Spelling Variance Checker (LLM)

Usage:
  powershell tools/llm-word.ps1 [OPTIONS]

Options:
  -quick              Run fast static check only (TF-IDF + Levenshtein + Embedding cache; skips LLM).
  -maxPairs <N>       Maximum number of candidate pairs to judge via LLM (default: 20, 0 for unlimited).
  -threshold <F>      Cosine similarity threshold for linking (default: 0.80).
  -backend <name>     LLM backend override (sakura, openrouter, ollama, mock).
  -model <name>       LLM / Embedding model name override.
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
             "python", "-m", "spec_integrator.cli", "llm-word",
             "--config", $config, "--threshold", "$threshold",
             "--max-pairs", "$maxPairs")
if ($quick) { $cmdArgs += "--quick" }
if ($backend) { $cmdArgs += @("--backend", $backend) }
if ($model) { $cmdArgs += @("--model", $model) }

& uv @cmdArgs
exit $LASTEXITCODE
