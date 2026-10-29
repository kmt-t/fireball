[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$prefix,
    [string]$config = "spec-integrator.yaml"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

& uv run --project (Join-Path $scriptDir "spec-integrator") python -m spec_integrator.cli `
    next-id --config $config $prefix
exit $LASTEXITCODE
