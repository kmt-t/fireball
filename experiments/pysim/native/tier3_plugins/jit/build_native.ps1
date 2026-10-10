# Builds the required native C++ x64 JIT compiler and invocation bridge
# (Windows / clang-cl with Clang's assembler).
# Requires clang-cl and clang++ on PATH and a Visual Studio Build Tools +
# Windows SDK install (for the MSVC headers/import libs clang-cl targets).
param([switch]$QA, [switch]$BlockCounters)

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
$clangClPath = (Get-Command clang-cl.exe).Source
$clangPath = (Get-Command clang.exe).Source
$clangxxPath = (Get-Command clang++.exe).Source

$env:PYTHONPATH = Join-Path $projectRoot "experiments\pysim\tier1_core"
$fastSlotCount = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_CACHE_FAST_SLOT_COUNT; print(JIT_CACHE_FAST_SLOT_COUNT)"
$cacheRuntimeConfig = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_CACHE_BANK_ENTRY_CAPACITY; print(JIT_CACHE_BANK_ENTRY_CAPACITY)"
$cacheRuntimeValues = (($cacheRuntimeConfig -join " ").Trim() -split "\s+")
$bankEntries = $cacheRuntimeValues[0]
$nativeJitConfig = & uv run --offline --no-sync --python $uvPython python -c "from config import JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET, JIT_X64_COMMON_CODE_RELATIVE_OFFSET; print(JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET, JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_X64_TRACE_HEADER_BYTES, JIT_X64_CHAIN_TARGET_OFFSET, JIT_X64_COMMON_CODE_RELATIVE_OFFSET)"
$nativeJitValues = (($nativeJitConfig -join " ").Trim() -split "\s+")
$chainDispatchOffset = $nativeJitValues[0]
$chainDispatchBytes = $nativeJitValues[1]
$traceHeaderBytes = $nativeJitValues[2]
$chainTargetOffset = $nativeJitValues[3]
$commonRelativeOffset = $nativeJitValues[4]
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native"
New-Item -ItemType Directory -Force -Path $nativeBuildDir | Out-Null
$stencilOutputDir = Join-Path $nativeBuildDir "jit-stencils"
$nativeSourceDir = $scriptDir
$pythonPackageDir = Join-Path $projectRoot "experiments\pysim\tier3_plugins\jit"
$sourceCpp = Join-Path $nativeSourceDir "trace_compiler.cxx"
$commonSourceCpp = Join-Path $nativeSourceDir "common_code.cxx"
$memorySourceCpp = Join-Path $nativeSourceDir "executable_memory.cxx"
$runtimeSourceCpp = Join-Path $nativeSourceDir "jit_runtime.cxx"
$profilingSourceCpp = Join-Path $nativeSourceDir "profiling.cxx"
$generatedDll = Join-Path $nativeBuildDir "trace_compiler.dll"

$commonConfig = & uv run --offline --no-sync --python $uvPython python -c 'import config; names=(''JIT_CACHE_REGION_BYTES'', ''JIT_CACHE_COMMON_CODE_BYTES'', ''JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET'', ''JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES'', ''JIT_HISTORY_CAPACITY'', ''JIT_COMPILE_QUEUE_CAPACITY'', ''JIT_CARD_SHIFT'', ''JIT_CACHE_BANK_CAPACITY_BYTES'', ''JIT_CACHE_ACTIVE_OFFSET_BYTES'', ''JIT_CACHE_WARM_OFFSET_BYTES'', ''JIT_CACHE_OLDEST_OFFSET_BYTES'', ''FB_CONF_JIT_AGING_STEP_UNITS'', ''FB_CONF_JIT_AGING_STEP_SCAN_BYTES''); print(" ".join("/D"+(name if name.startswith("FB_CONF_") else "FB_CONF_"+name)+"="+str(getattr(config,name)) for name in names))'
$commonDefines = (($commonConfig -join " ").Trim() -split "\s+")

if ($QA) {
    $runtimeSourceCpp = Join-Path $projectRoot "experiments\pysim\qa\private\native_jit_probe.cxx"
    $generatedDll = Join-Path $nativeBuildDir "jit_probe.dll"
    $pythonPackageDir = Join-Path $projectRoot "experiments\pysim\qa\private"
}
if ($BlockCounters) { $commonDefines += "/DFB_CONF_JIT_BLOCK_COUNTERS=1" }

$assemblyStencilGenerator = Join-Path $projectRoot "tools\jit_stencil_gen\generate_assembly.py"
$traceStencilSource = Join-Path $nativeSourceDir "lir\x64\trace.S"
Write-Host ">>> Assembling x64 JIT trace stencils" -ForegroundColor Yellow
& uv run --offline --no-sync --python $uvPython python $assemblyStencilGenerator $traceStencilSource `
    --role trace --output-dir $stencilOutputDir `
    --header stencils.hxx --binary stencils.bin --namespace generated `
    --assembly-output (Join-Path $stencilOutputDir "trace_stencils.preprocessed.S") `
    --clang $clangPath --clangxx $clangxxPath --target x86_64-pc-windows-msvc
if ($LASTEXITCODE -ne 0) { throw "x64 trace stencil generation failed" }

Write-Host ">>> Assembling optimized x64 operation stencils" -ForegroundColor Yellow
$assemblyStencilSource = Join-Path $nativeSourceDir "lir\x64\operations.S"
& uv run --offline --no-sync --python $uvPython python $assemblyStencilGenerator $assemblyStencilSource `
    --role operations --output-dir $stencilOutputDir `
    --header asm_stencils.hxx --binary asm_stencils.bin --namespace asm_generated `
    --assembly-output (Join-Path $stencilOutputDir "operations.preprocessed.S") `
    --clang $clangPath --clangxx $clangxxPath --target x86_64-pc-windows-msvc
if ($LASTEXITCODE -ne 0) { throw "x64 operation stencil generation failed" }

Write-Host ">>> Assembling shared x64 JIT code area" -ForegroundColor Yellow
$commonCodeSource = Join-Path $nativeSourceDir "lir\x64\common.S"
& uv run --offline --no-sync --python $uvPython python $assemblyStencilGenerator $commonCodeSource `
    --role common --output-dir $stencilOutputDir `
    --header common_code.hxx --binary common_code.bin --namespace generated `
    --assembly-output (Join-Path $stencilOutputDir "common_code.preprocessed.S") `
    --clang $clangPath --clangxx $clangxxPath --target x86_64-pc-windows-msvc
if ($LASTEXITCODE -ne 0) { throw "x64 common code generation failed" }

$linkerArgs = @(
    "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64",
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64",
    "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
)
if ($QA) {
    $qaExportDef = Join-Path $projectRoot "experiments\pysim\qa\private\jit_probe_exports.def"
    $linkerArgs += "/DEF:$qaExportDef"
}

if ($QA) {
    Write-Host ">>> Compiling QA Tier 3 JIT -> jit_probe.dll (clang-cl)" -ForegroundColor Yellow
} else {
    Write-Host ">>> Compiling trace_compiler.cxx -> trace_compiler.dll (clang-cl)" -ForegroundColor Yellow
}
& clang-cl.exe /TP /std:c++latest /O2 /LD `
    "/DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET=$chainDispatchOffset" `
    "/DFB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES=$chainDispatchBytes" `
    "/DFB_CONF_JIT_X64_TRACE_HEADER_BYTES=$traceHeaderBytes" `
    "/DFB_CONF_JIT_X64_CHAIN_TARGET_OFFSET=$chainTargetOffset" `
    "/DFB_CONF_JIT_X64_COMMON_CODE_RELATIVE_OFFSET=$commonRelativeOffset" `
    "/DFB_CONF_JIT_CACHE_FAST_SLOT_COUNT=$fastSlotCount" `
    "/DFB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY=$bankEntries" `
    -fno-exceptions -fno-rtti `
    "-I$nativeSourceDir" `
    "-I$stencilOutputDir" `
    "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
    "-I$sdkRoot\Include\$sdkVer\ucrt" `
    "-I$sdkRoot\Include\$sdkVer\shared" `
    "-I$sdkRoot\Include\$sdkVer\um" `
    @commonDefines $sourceCpp $commonSourceCpp $memorySourceCpp $runtimeSourceCpp $profilingSourceCpp `
    /Fe:$generatedDll `
    /link @linkerArgs
if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed" }

Copy-Item -Force $generatedDll (Join-Path $pythonPackageDir (Split-Path -Leaf $generatedDll))
if ($QA) {
    Write-Host "✔ Built QA jit_probe.dll" -ForegroundColor Green
} else {
    Write-Host "✔ Built trace_compiler.dll" -ForegroundColor Green
}
