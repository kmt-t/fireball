# Fireball Document Quality Gate & Verification Runner (PowerShell)
# Runs static verifications and quality gates for documentation without calling LLMs.
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments)]
    [string[]]$files = @(),
    [string]$report = "reports/doc_report.md",
    [switch]$clean,
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Document Quality Check

Usage:
  powershell tools/check-doc.ps1 [OPTIONS] [FILES...]

Options:
  -report <path> Path to generated markdown report (default: reports/doc_report.md).
  -clean         Run clean verification without using cached assessment/graph state.
  -config <path> Path to configuration file (default: spec-integrator.yaml).
  -h, -help      Show this help message.
"@
    exit 0
}

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$reportsDir = Split-Path -Parent $report
if ($reportsDir -and -not (Test-Path $reportsDir)) {
    New-Item -ItemType Directory -Path $reportsDir | Out-Null
}

$cmdArgs = @("run", "--system-certs", "--project", "tools/spec-integrator",
             "python", "-m", "spec_integrator.cli", "check-doc",
             "--config", $config, "--report", $report)
if ($clean) { $cmdArgs += "--clean" }

if ($files.Count -gt 0) {
    $cmdArgs += $files
} else {
    $found = Get-ChildItem -Path "docs" -Filter "*.md" -Recurse | ForEach-Object { Resolve-Path -Relative $_.FullName }
    $cmdArgs += @($found)
}

& uv @cmdArgs
exit $LASTEXITCODE