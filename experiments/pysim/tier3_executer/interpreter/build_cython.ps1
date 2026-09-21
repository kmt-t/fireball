# Builds the optional Cython pure-Python-mode Tier 3 interpreter accelerator.
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native"
New-Item -ItemType Directory -Force $nativeBuildDir | Out-Null

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products '*' -property installationPath
if (-not $vsDir) { throw "Visual Studio Build Tools not found (vswhere returned nothing)." }
$msvcVer = Get-ChildItem "$vsDir\VC\Tools\MSVC" | Select-Object -First 1 -ExpandProperty Name
$sdkRoot = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = Get-ChildItem "$sdkRoot\Include" | Select-Object -Last 1 -ExpandProperty Name
$pyInc = & uv run python -c "import sysconfig; print(sysconfig.get_path('include'))"
$pyLibDir = & uv run python -c "import sys, os; print(os.path.join(sys.base_prefix, 'libs'))"
$generatedC = Join-Path $nativeBuildDir "interpreter.c"
$outputPyd = Join-Path $scriptDir "interpreter.pyd"

Write-Host ">>> Transpiling interpreter.py -> $generatedC (Cython)" -ForegroundColor Yellow
& uv run python -B -m cython (Join-Path $scriptDir "interpreter.py") -3 -o $generatedC
if ($LASTEXITCODE -ne 0) { throw "cython transpile failed for interpreter" }

Write-Host ">>> Compiling interpreter.c -> interpreter.pyd (clang-cl)" -ForegroundColor Yellow
& clang-cl.exe /O2 /LD /EHsc `
    "-I$pyInc" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    $generatedC /Fe:$outputPyd `
    /link `
    "/LIBPATH:$pyLibDir" `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed for interpreter" }
Write-Host "✔ Built $outputPyd" -ForegroundColor Green
