# Builds the optional native_trace_call Cython accelerator (Windows / clang-cl).
# See native_trace_call.pyx for what this replaces. Requires: `uv pip install cython`
# (already in requirements.txt), clang-cl on PATH, and a Visual Studio Build Tools +
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

Write-Host ">>> Transpiling native_trace_call.pyx -> .c (Cython)" -ForegroundColor Yellow
& uv run cython native_trace_call.pyx -3 -o native_trace_call.c
if ($LASTEXITCODE -ne 0) { throw "cython transpile failed" }

Write-Host ">>> Compiling native_trace_call.c -> .pyd (clang-cl)" -ForegroundColor Yellow
& clang-cl.exe /O2 /LD /EHsc `
    "-I$pyInc" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    native_trace_call.c /Fe:native_trace_call.pyd `
    /link `
    "/LIBPATH:$pyLibDir" `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed" }

Write-Host "✔ Built native_trace_call.pyd" -ForegroundColor Green
