# Builds the required native C++ x64 Copy-and-Patch JIT compiler and invocation bridge
# (Windows / clang-cl).
# Requires clang-cl on PATH and a Visual Studio Build Tools +
# Windows SDK install (for the MSVC headers/import libs clang-cl targets).
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$projectRoot = (Resolve-Path (Join-Path $scriptDir "..\..\..\..")).Path
$uvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products '*' -property installationPath
if (-not $vsDir) { throw "Visual Studio Build Tools not found (vswhere returned nothing)." }
$msvcVer = Get-ChildItem "$vsDir\VC\Tools\MSVC" | Select-Object -First 1 -ExpandProperty Name
$sdkRoot = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = Get-ChildItem "$sdkRoot\Include" | Select-Object -Last 1 -ExpandProperty Name

$env:PYTHONPATH = Join-Path $projectRoot "experiments\pysim\tier1_core"
$fastSlotCount = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_CACHE_FAST_SLOT_COUNT; print(JIT_CACHE_FAST_SLOT_COUNT)"
$nativeJitConfig = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET; print(JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET)"
$nativeJitValues = (($nativeJitConfig -join " ").Trim() -split "\s+")
$chainDispatchOffset = $nativeJitValues[0]
$chainDispatchBytes = $nativeJitValues[1]
$commonEpilogueOffset = $nativeJitValues[2]
$traceHeaderBytes = $nativeJitValues[3]
$chainTargetOffset = $nativeJitValues[4]
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native"
New-Item -ItemType Directory -Force -Path $nativeBuildDir | Out-Null
$nativeSourceDir = Join-Path $projectRoot "experiments\pysim\native\tier3_executer\jit"
$sourceCpp = Join-Path $nativeSourceDir "trace_compiler.cxx"
$generatedDll = Join-Path $nativeBuildDir "trace_compiler.dll"

Write-Host ">>> Compiling trace_compiler.cxx -> trace_compiler.dll (clang-cl)" -ForegroundColor Yellow
& clang-cl.exe /TP /std:c++latest /O2 /LD `
    "/DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET=$chainDispatchOffset" `
    "/DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES=$chainDispatchBytes" `
    "/DFB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET=$commonEpilogueOffset" `
    "/DFB_CONF_JIT_X64_TRACE_HEADER_BYTES=$traceHeaderBytes" `
    "/DFB_CONF_JIT_X64_CHAIN_TARGET_OFFSET=$chainTargetOffset" `
    "-I$nativeSourceDir" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    $sourceCpp /Fe:$generatedDll `
    /link `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed" }

Copy-Item -Force $generatedDll (Join-Path $scriptDir "trace_compiler.dll")
Write-Host "✔ Built trace_compiler.dll" -ForegroundColor Green

$cacheSourceCpp = Join-Path $nativeSourceDir "fast_cache.cxx"
$cacheGeneratedDll = Join-Path $nativeBuildDir "fast_cache.dll"
Write-Host ">>> Compiling fast_cache.cxx (fixed JIT lookup slots) -> fast_cache.dll (clang-cl)" -ForegroundColor Yellow
& clang-cl.exe /TP /std:c++latest /O2 /LD `
    "/DFB_CONF_JIT_CACHE_FAST_SLOT_COUNT=$fastSlotCount" `
    "-I$nativeSourceDir" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    $cacheSourceCpp /Fe:$cacheGeneratedDll `
    /link `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl fast cache compile failed" }

Copy-Item -Force $cacheGeneratedDll (Join-Path $scriptDir "fast_cache.dll")
Write-Host "✔ Built fast_cache.dll with $fastSlotCount slots" -ForegroundColor Green
