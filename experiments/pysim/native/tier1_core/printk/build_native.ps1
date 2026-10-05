# Run in a Visual Studio developer shell with clang-cl available.
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = (Resolve-Path (Join-Path $scriptDir "..\..\..\..\..")).Path
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native"
New-Item -ItemType Directory -Force -Path $nativeBuildDir | Out-Null
$library = Join-Path $nativeBuildDir "printk.dll"
Push-Location $nativeBuildDir
try {
    & clang-cl /std:c++latest /O2 /LD -fno-exceptions -fno-rtti `
        (Join-Path $scriptDir "printk.cxx") "/Fe:$library"
    if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed" }
    Copy-Item -Force $library (Join-Path $projectRoot "experiments\pysim\tier1_core\printk.dll")
} finally {
    Pop-Location
}
