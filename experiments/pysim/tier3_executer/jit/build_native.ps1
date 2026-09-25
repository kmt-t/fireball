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

$pyInc = & uv run --offline --no-sync --python $uvPython python -c "import sysconfig; print(sysconfig.get_path('include'))"
$pyLibDir = & uv run --offline --no-sync --python $uvPython python -c "import sys, os; print(os.path.join(sys.base_prefix, 'libs'))"
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
$sourceCpp = Join-Path $scriptDir "native_trace_call.cxx"
$generatedPyd = Join-Path $nativeBuildDir "native_trace_call.pyd"

Write-Host ">>> Compiling native_trace_call.cxx (native JIT compiler) -> .pyd (clang-cl)" -ForegroundColor Yellow
& clang-cl.exe /TP /std:c++latest /O2 /LD /EHsc `
    "/DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET=$chainDispatchOffset" `
    "/DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES=$chainDispatchBytes" `
    "/DFB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET=$commonEpilogueOffset" `
    "/DFB_CONF_JIT_X64_TRACE_HEADER_BYTES=$traceHeaderBytes" `
    "/DFB_CONF_JIT_X64_CHAIN_TARGET_OFFSET=$chainTargetOffset" `
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

$cacheSourceCpp = Join-Path $scriptDir "native_fast_cache.cxx"
$cacheGeneratedPyd = Join-Path $nativeBuildDir "_jit_cache_native.pyd"
Write-Host ">>> Compiling native_fast_cache.cxx (fixed JIT lookup slots) -> .pyd (clang-cl)" -ForegroundColor Yellow
& clang-cl.exe /TP /std:c++latest /O2 /LD /EHsc `
    "/DFB_CONF_JIT_ENABLED=1" `
    "/DFB_CONF_JIT_CACHE_FAST_SLOT_COUNT=$fastSlotCount" `
    "-I$pyInc" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    $cacheSourceCpp /Fe:$cacheGeneratedPyd `
    /link `
    "/LIBPATH:$pyLibDir" `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl fast cache compile failed" }

Copy-Item -Force $cacheGeneratedPyd (Join-Path $scriptDir "_jit_cache_native.pyd")
Write-Host "✔ Built _jit_cache_native.pyd with $fastSlotCount slots" -ForegroundColor Green
