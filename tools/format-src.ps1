# Fireball Source Code Formatter (PowerShell)
# Applies formatters (Ruff for Python, clang-format for C++) without calling LLMs.
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments)]
    [string[]]$files = @(),
    [string]$group = "all",
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Source Code Formatter

Usage:
  powershell tools/format-src.ps1 [OPTIONS] [FILES...]

Options:
  -group <group> Source group to format: cpp, python, concepts, formal, pysim, all (default: all).
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
             "python", "-m", "spec_integrator.cli", "format-src",
             "--config", $config, "--group", $group)

if ($files.Count -gt 0) {
    $cmdArgs += $files
} else {
    $collected = @()
    $grp = $group.ToLowerInvariant()
    if ($grp -eq "all" -or $grp -eq "cpp") {
        foreach ($d in @("inc", "src")) {
            if (Test-Path $d) {
                Get-ChildItem -Path $d -Include "*.hxx","*.cxx","*.c","*.h","*.cpp" -Recurse | ForEach-Object { $collected += (Resolve-Path -Relative $_.FullName) }
            }
        }
    }
    if ($grp -eq "all" -or $grp -eq "python" -or $grp -eq "concepts") {
        if (Test-Path "docs") {
            Get-ChildItem -Path "docs" -Filter "*_concept.py" -Recurse | ForEach-Object { $collected += (Resolve-Path -Relative $_.FullName) }
        }
    }
    if ($grp -eq "all" -or $grp -eq "python" -or $grp -eq "formal") {
        if (Test-Path "docs") {
            Get-ChildItem -Path "docs" -Filter "*_model.py" -Recurse | ForEach-Object { $collected += (Resolve-Path -Relative $_.FullName) }
            Get-ChildItem -Path "docs" -Include "*.py" -Recurse | Where-Object { $_.FullName -like "*formal*" } | ForEach-Object { $collected += (Resolve-Path -Relative $_.FullName) }
        }
    }
    if ($grp -eq "all" -or $grp -eq "python" -or $grp -eq "pysim") {
        if (Test-Path "experiments/pysim") {
            Get-ChildItem -Path "experiments/pysim" -Filter "*.py" -Recurse | ForEach-Object { $collected += (Resolve-Path -Relative $_.FullName) }
        }
    }
    $cmdArgs += ($collected | Select-Object -Unique)
}

& uv @cmdArgs
exit $LASTEXITCODE