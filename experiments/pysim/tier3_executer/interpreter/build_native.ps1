# Build the required native Tier 3 handler table (Windows / clang-cl).
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$projectRoot = (Resolve-Path (Join-Path $scriptDir "..\..\..\..")).Path
$uvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products '*' -property installationPath
if (-not $vsDir) { throw "Visual Studio Build Tools not found." }
$msvcVer = Get-ChildItem "$vsDir\VC\Tools\MSVC" | Select-Object -First 1 -ExpandProperty Name
$sdkRoot = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = Get-ChildItem "$sdkRoot\Include" | Select-Object -Last 1 -ExpandProperty Name
$env:PYTHONPATH = Join-Path $projectRoot "experiments\pysim\tier1_core"
$dispatchConfig = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_CACHE_BANK_CAPACITY_BYTES, JIT_CACHE_BANK_COUNT, JIT_X64_TRACE_HEADER_BYTES; print(JIT_CACHE_BANK_COUNT * max(1, JIT_CACHE_BANK_CAPACITY_BYTES // JIT_X64_TRACE_HEADER_BYTES))"
$blockConfig = & uv run --offline --no-sync --python $uvPython python -c "from config import FB_CONF_MAX_BASIC_BLOCKS; print(FB_CONF_MAX_BASIC_BLOCKS)"
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native"
New-Item -ItemType Directory -Force -Path $nativeBuildDir | Out-Null
$nativeSourceDir = Join-Path $projectRoot "experiments\pysim\native\tier3_executer\interpreter"
$sourceCxx = Join-Path $nativeSourceDir "native_interpreter.cxx"
$outputDll = Join-Path $nativeBuildDir "native_interpreter.dll"

Write-Host ">>> Compiling native_interpreter.cxx -> native_interpreter.dll" -ForegroundColor Yellow
& clang-cl.exe /TP /std:c++latest /O2 /LD /W4 `
    "/DFB_CONF_NATIVE_JIT_TRACE_CAPACITY=$dispatchConfig" `
    "/DFB_CONF_NATIVE_JIT_BLOCK_CAPACITY=$blockConfig" `
    "-I$nativeSourceDir" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    $sourceCxx /Fe:$outputDll `
    /link `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed" }
Copy-Item -Force $outputDll (Join-Path $scriptDir "native_interpreter.dll")
Write-Host "✔ Built native_interpreter.dll" -ForegroundColor Green
