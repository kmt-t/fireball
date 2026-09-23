# Fireball stored LLM finding query (PowerShell)
# Queries saved review decisions; does not call an LLM API.
param(
    [double]$minConfidence = 0.70,
    [string[]]$classification = @(),
    [switch]$allOutcomes,
    [string]$runType = "",
    [int]$limit = 200,
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Stored LLM Finding Query

Usage:
  powershell tools/llm-findings.ps1 [OPTIONS]

Options:
  -minConfidence <0..1> Minimum confidence (default: 0.70).
  -classification <id>  Filter by outcome; may be repeated.
  -allOutcomes          Include no_issue and insufficient_context outcomes.
  -runType <type>       Filter by review command/mode.
  -limit <N>            Maximum rows; 0 prints all matches (default: 200).
  -config <path>        Path to configuration file (default: spec-integrator.yaml).
  -h, -help             Show this help message.
"@
    exit 0
}

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$cmdArgs = @("run", "--system-certs", "--project", "tools/spec-integrator",
             "python", "-m", "spec_integrator.cli", "llm-findings",
             "--config", $config, "--min-confidence", "$minConfidence",
             "--limit", "$limit")
foreach ($value in $classification) { $cmdArgs += @("--classification", $value) }
if ($allOutcomes) { $cmdArgs += "--all-outcomes" }
if ($runType) { $cmdArgs += @("--run-type", $runType) }

& uv @cmdArgs
exit $LASTEXITCODE
