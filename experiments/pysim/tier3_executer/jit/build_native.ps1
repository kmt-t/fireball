# Builds the optional native_trace_call C++ accelerator (Windows / clang-cl).
# Requires clang-cl on PATH and a Visual Studio Build Tools +
# Windows SDK install (for the MSVC headers/import libs clang-cl targets).
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products '*' -property installationPath
if (-not $vsDir) { throw "Visual Studio Build Tools not found (vswhere returned nothing)." }
$msvcVer = Get-ChildItem "$vsDir\VC\Tools\MSVC" | Select-Object -First 1 -ExpandProperty Name
$sdkRoot = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = Get-ChildItem "$sdkRoot\Include" | Select-Object -Last 1 -ExpandProperty Name

$pyInc = & uv run python -c "import sysconfig; print(sysconfig.get_path('include'))"
$pyLibDir = & uv run python -c "import sys, os; print(os.path.join(sys.base_prefix, 'libs'))"
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native"
New-Item -ItemType Directory -Force -Path $nativeBuildDir | Out-Null
$sourceCpp = Join-Path $scriptDir "native_trace_call.cxx"
$generatedPyd = Join-Path $nativeBuildDir "native_trace_call.pyd"

Write-Host ">>> Compiling native_trace_call.cxx -> .pyd (clang-cl)" -ForegroundColor Yellow
& clang-cl.exe /TP /O2 /LD /EHsc `
    "-I$pyInc" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    $sourceCpp /Fe:$generatedPyd `
    /link `
    "/LIBPATH:$pyLibDir" `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed" }

Copy-Item -Force $generatedPyd (Join-Path $scriptDir "native_trace_call.pyd")
Write-Host "✔ Built native_trace_call.pyd" -ForegroundColor Green
