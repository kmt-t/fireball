# Fireball Source Code Quality Gate & Verification Runner (PowerShell)
# Runs static code checks, anti-sabotage rules, and tests without calling LLMs.
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments)]
    [string[]]$files = @(),
    [string]$group = "all",
    [string]$config = "spec-integrator.yaml",
    [switch]$optimized,
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Source Code Quality Check

Usage:
  powershell tools/check-src.ps1 [OPTIONS] [FILES...]

Options:
  -group <group> Source group to verify: cpp, python, concepts, formal, pysim, all (default: all).
  -config <path> Path to configuration file (default: spec-integrator.yaml).
  -optimized    Compare valid PySIM/QA behavior under Python 0, -O, and -OO.
  -h, -help      Show this help message.
"@
    exit 0
}

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$cmdArgs = @("run", "--system-certs", "--project", "tools/spec-integrator",
             "python", "-m", "spec_integrator.cli", "check-src",
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
$sourceExit = $LASTEXITCODE
& (Join-Path $scriptDir "check-verification-matrix.ps1") -config $config
$matrixExit = $LASTEXITCODE
$sizeExit = 0
if ($group.ToLowerInvariant() -in @("all", "cpp")) {
    & uv run --group shared python tools/check_source_size.py
    $sizeExit = $LASTEXITCODE
}
$optimizedExit = 0
if ($optimized) {
    & uv run --group shared --group qa-shared --group qa-private python experiments/pysim/qa/run_all.py --optimized
    $optimizedExit = $LASTEXITCODE
}
if ($sourceExit -ne 0 -or $matrixExit -ne 0 -or $sizeExit -ne 0 -or $optimizedExit -ne 0) { exit 1 }
exit 0
