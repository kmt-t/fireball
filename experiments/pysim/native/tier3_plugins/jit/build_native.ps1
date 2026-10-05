# Builds the required native C++ x64 Copy-and-Patch JIT compiler and invocation bridge
# (Windows / clang-cl).
# Requires clang-cl on PATH and a Visual Studio Build Tools +
# Windows SDK install (for the MSVC headers/import libs clang-cl targets).
param([switch]$QA)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$projectRoot = (Resolve-Path (Join-Path $scriptDir "..\..\..\..\..")).Path
$uvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products '*' -property installationPath
if (-not $vsDir) { throw "Visual Studio Build Tools not found (vswhere returned nothing)." }
$msvcVer = Get-ChildItem "$vsDir\VC\Tools\MSVC" | Select-Object -First 1 -ExpandProperty Name
$sdkRoot = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = Get-ChildItem "$sdkRoot\Include" | Select-Object -Last 1 -ExpandProperty Name

$env:PYTHONPATH = Join-Path $projectRoot "experiments\pysim\tier1_core"
$fastSlotCount = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_CACHE_FAST_SLOT_COUNT; print(JIT_CACHE_FAST_SLOT_COUNT)"
$cacheRuntimeConfig = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_CACHE_BANK_ENTRY_CAPACITY, JIT_CACHE_MAX_INBOUND_SOURCES; print(JIT_CACHE_BANK_ENTRY_CAPACITY, JIT_CACHE_MAX_INBOUND_SOURCES)"
$cacheRuntimeValues = (($cacheRuntimeConfig -join " ").Trim() -split "\s+")
$bankEntries = $cacheRuntimeValues[0]
$maxInbound = $cacheRuntimeValues[1]
$nativeJitConfig = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET, JIT_X64_TRACE_ENTRY_STUB_BYTES; print(JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_EPILOGUE_OFFSET, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET, JIT_X64_TRACE_ENTRY_STUB_BYTES)"
$nativeJitValues = (($nativeJitConfig -join " ").Trim() -split "\s+")
$chainDispatchOffset = $nativeJitValues[0]
$chainDispatchBytes = $nativeJitValues[1]
$commonEpilogueOffset = $nativeJitValues[2]
$traceHeaderBytes = $nativeJitValues[3]
$chainTargetOffset = $nativeJitValues[4]
$entryStubBytes = $nativeJitValues[5]
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native"
New-Item -ItemType Directory -Force -Path $nativeBuildDir | Out-Null
$nativeSourceDir = $scriptDir
$pythonPackageDir = Join-Path $projectRoot "experiments\pysim\tier3_plugins\jit"
$sourceCpp = Join-Path $nativeSourceDir "trace_compiler.cxx"
$commonSourceCpp = Join-Path $nativeSourceDir "common_code.cxx"
$memorySourceCpp = Join-Path $nativeSourceDir "executable_memory.cxx"
$runtimeSourceCpp = Join-Path $nativeSourceDir "jit_runtime.cxx"
$profilingSourceCpp = Join-Path $nativeSourceDir "profiling.cxx"
$generatedDll = Join-Path $nativeBuildDir "trace_compiler.dll"

$commonConfig = & uv run --offline --no-sync --python $uvPython python -c 'import config; names=(''JIT_CACHE_REGION_BYTES'', ''JIT_CACHE_COMMON_CODE_BYTES'', ''JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES'', ''JIT_TRACE_COMMON_PROLOGUE_OFFSET'', ''JIT_TRACE_COMMON_HELPER_OFFSET'', ''JIT_TRACE_HELPER_ENTRY_BYTES'', ''JIT_TRACE_TYPED_I32_HELPER_COUNT'', ''JIT_TRACE_TYPED_I32_HELPER_OFFSET'', ''JIT_TRACE_WIDE_HELPER_COUNT'', ''JIT_TRACE_WIDE_HELPER_OFFSET'', ''JIT_X64_HELPER_TARGET_OFFSET'', ''JIT_X64_TRACE_ENTRY_STUB_BYTES'', ''JIT_HISTORY_CAPACITY'', ''JIT_COMPILE_QUEUE_CAPACITY'', ''JIT_CARD_SHIFT'', ''JIT_CACHE_BANK_CAPACITY_BYTES'', ''JIT_CACHE_ACTIVE_OFFSET_BYTES'', ''JIT_CACHE_WARM_OFFSET_BYTES'', ''JIT_CACHE_OLDEST_OFFSET_BYTES'', ''FB_CONF_JIT_AGING_STEP_UNITS'', ''FB_CONF_JIT_AGING_STEP_SCAN_BYTES''); print(" ".join("/D"+(name if name.startswith("FB_CONF_") else "FB_CONF_"+name)+"="+str(getattr(config,name)) for name in names))'
$commonDefines = (($commonConfig -join " ").Trim() -split "\s+")

if ($QA) {
    $commonDefines += "/DFB_PYSIM_QA"
    $runtimeSourceCpp = Join-Path $projectRoot "experiments\pysim\qa\private\native_jit_probe.cxx"
    $generatedDll = Join-Path $nativeBuildDir "jit_probe.dll"
    $pythonPackageDir = Join-Path $projectRoot "experiments\pysim\qa\private"
}

Write-Host ">>> Compiling trace_compiler.cxx -> trace_compiler.dll (clang-cl)" -ForegroundColor Yellow
& clang-cl.exe /TP /std:c++latest /O2 /LD `
    "/DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET=$chainDispatchOffset" `
    "/DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES=$chainDispatchBytes" `
    "/DFB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET=$commonEpilogueOffset" `
    "/DFB_CONF_JIT_X64_TRACE_HEADER_BYTES=$traceHeaderBytes" `
    "/DFB_CONF_JIT_X64_CHAIN_TARGET_OFFSET=$chainTargetOffset" `
    "/DFB_CONF_JIT_CACHE_FAST_SLOT_COUNT=$fastSlotCount" `
    "/DFB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY=$bankEntries" `
    "/DFB_CONF_JIT_CACHE_MAX_INBOUND_SOURCES=$maxInbound" `
    -fno-exceptions -fno-rtti `
    "-I$nativeSourceDir" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    @commonDefines $sourceCpp $commonSourceCpp $memorySourceCpp $runtimeSourceCpp $profilingSourceCpp /Fe:$generatedDll `
    /link `
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed" }

Copy-Item -Force $generatedDll (Join-Path $pythonPackageDir (Split-Path -Leaf $generatedDll))
Write-Host "✔ Built trace_compiler.dll" -ForegroundColor Green
