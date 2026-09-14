# Fireball strict concept/test/scenario coverage matrix gate.
[CmdletBinding()]
param(
    [string]$config = "spec-integrator.yaml",
    [switch]$h,
    [switch]$help
)

if ($h -or $help) {
    Write-Host @"
Fireball Verification Matrix Check

Usage:
  powershell tools/check-verification-matrix.ps1
"@
    exit 0
}

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

& uv run --system-certs --project tools/spec-integrator python tools/check_verification_matrix.py $config
exit $LASTEXITCODE
