# Fireball Content Complexity & Risk Assessment Runner (PowerShell)
# Scores requirement/design keywords complexity and design risk via LLM.
param(
    [int]$maxKeywords = 15,
    [switch]$exhaustive,
    [int]$minReferences = 0,
    [string]$backend = "",
    [string]$model = "",
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Keyword Risk Assessment (LLM)

Usage:
  powershell tools/risk.ps1 [OPTIONS]

Options:
  -maxKeywords <N>    Maximum keywords to assess (default: 15, 0 for unlimited).
  -exhaustive         Assess all keywords without limit.
  -minReferences <N>  Minimum referencing sections required to include a keyword (default: 0).
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
             "python", "-m", "spec_integrator.cli", "risk",
             "--config", $config, "--max-keywords", "$maxKeywords",
             "--min-references", "$minReferences")
if ($exhaustive) { $cmdArgs += "-a" }
if ($backend) { $cmdArgs += @("--backend", $backend) }
if ($model) { $cmdArgs += @("--model", $model) }

& uv @cmdArgs
exit $LASTEXITCODE
