# Build the complete Tier 3 interpreter as one Cython CPS translation unit.
#
# The native CPS chain lives in cps_chain.pyx.  Opcode bodies remain in the
# ordinary interpreter.py handler table.  This script compiles the chain as
# _interpreter_cps_native.pyd; no reduced opcode probe is used by AO-Bench.
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native-cps"
New-Item -ItemType Directory -Force $nativeBuildDir | Out-Null

$generatedPyx = Join-Path $scriptDir "cps_chain.pyx"
$generatedC = Join-Path $nativeBuildDir "_interpreter_cps_native.c"
$outputPyd = Join-Path $nativeBuildDir "_interpreter_cps_native.pyd"
$outputDll = Join-Path $nativeBuildDir "_interpreter_cps_native.dll"

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products '*' -property installationPath
if (-not $vsDir) { throw "Visual Studio Build Tools not found (vswhere returned nothing)." }
$msvcVer = Get-ChildItem "$vsDir\VC\Tools\MSVC" | Select-Object -First 1 -ExpandProperty Name
$sdkRoot = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = Get-ChildItem "$sdkRoot\Include" | Select-Object -Last 1 -ExpandProperty Name
$pyInc = & uv run python -c "import sysconfig; print(sysconfig.get_path('include'))"
$pyLibDir = & uv run python -c "import sys, os; print(os.path.join(sys.base_prefix, 'libs'))"

Write-Host ">>> Transpiling C CPS handler chain (.pyx)" -ForegroundColor Yellow
& uv run python -B -m cython $generatedPyx -3 --module-name _interpreter_cps_native -o $generatedC
if ($LASTEXITCODE -ne 0) { throw "Cython transpile failed for complete interpreter CPS module" }

Write-Host ">>> Compiling native CPS interpreter (.pyd)" -ForegroundColor Yellow
& clang-cl.exe /TP /D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH /O2 /LD /EHsc `
    "-I$pyInc" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    $generatedC /Fe:$outputDll `
    /link `
    "/LIBPATH:$pyLibDir" `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed for complete interpreter CPS module" }
Copy-Item -Force $outputDll $outputPyd
Write-Host "Built $outputPyd" -ForegroundColor Green
