# Fireball Document Formatter (PowerShell)
# Normalizes markdown documents without calling LLMs.
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments)]
    [string[]]$files = @(),
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Document Formatter

Usage:
  powershell tools/format-doc.ps1 [OPTIONS] [FILES...]

Options:
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
             "python", "-m", "spec_integrator.cli", "format-doc", "--config", $config)

if ($files.Count -gt 0) {
    $cmdArgs += $files
} else {
    $found = Get-ChildItem -Path "docs" -Filter "*.md" -Recurse | ForEach-Object { Resolve-Path -Relative $_.FullName }
    $cmdArgs += @($found)
}

& uv @cmdArgs
exit $LASTEXITCODE