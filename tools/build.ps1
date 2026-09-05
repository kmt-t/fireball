# Fireball Specification Database Builder (PowerShell)
# Builds database and extracts TF-IDF candidate keywords/terms.
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments)]
    [string[]]$files = @(),
    [switch]$clean,
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Specification Database Builder

Usage:
  powershell tools/build.ps1 [OPTIONS] [FILES...]

Options:
  -clean         Clear cache DB and rebuild cleanly.
  -config <path> Path to configuration file (default: spec-integrator.yaml).
  -h, -help      Show this help message.
"@
    exit 0
}

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$cmdArgs = @("run", "--system-certs", "--project", "tools/spec-integrator",
             "python", "-m", "spec_integrator.cli", "build", "--config", $config)
if ($clean) { $cmdArgs += "--clean" }

if ($files.Count -gt 0) {
    $cmdArgs += $files
} else {
    $found = Get-ChildItem -Path "docs" -Filter "*.md" -Recurse | ForEach-Object { Resolve-Path -Relative $_.FullName }
    $cmdArgs += @($found)
}

& uv @cmdArgs
exit $LASTEXITCODE
