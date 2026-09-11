# Builds optional Cython pure-python-mode acceleration for pysim's hottest
# interpreter modules (Windows / clang-cl). See leb128.py / interpreter.py's
# `@cython.locals(...)`-annotated functions for what this compiles.
# Requires: `uv pip install cython` (already in requirements.txt), clang-cl
# on PATH, and a Visual Studio Build Tools + Windows SDK install (for the
# MSVC headers/import libs clang-cl targets).
#
# Not building this is always safe: `import cython` provides a no-op shim at
# runtime, so every module here runs as plain, unaccelerated Python with
# identical behavior when no .pyd is present -- Python simply prefers a
# same-named .pyd over the .py source once one exists alongside it.
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# Compile in dependency order: leb128 has no local deps; interpreter and
# runtime_engine import other pysim modules at the Python level (normal
# dynamic import, not a C link), so order between them doesn't matter, but
# leb128 must exist first since it's cimport-free pure Python either way.
$modules = @("leb128", "interpreter", "runtime_engine")

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products '*' -property installationPath
if (-not $vsDir) { throw "Visual Studio Build Tools not found (vswhere returned nothing)." }
$msvcVer = Get-ChildItem "$vsDir\VC\Tools\MSVC" | Select-Object -First 1 -ExpandProperty Name
$sdkRoot = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = Get-ChildItem "$sdkRoot\Include" | Select-Object -Last 1 -ExpandProperty Name

$pyInc = & uv run python -c "import sysconfig; print(sysconfig.get_path('include'))"
$pyLibDir = & uv run python -c "import sys, os; print(os.path.join(sys.base_prefix, 'libs'))"

foreach ($mod in $modules) {
    Write-Host ">>> Transpiling $mod.py -> .c (Cython)" -ForegroundColor Yellow
    & uv run cython "$mod.py" -3 -o "$mod.c"
    if ($LASTEXITCODE -ne 0) { throw "cython transpile failed for $mod" }

    Write-Host ">>> Compiling $mod.c -> .pyd (clang-cl)" -ForegroundColor Yellow
    & clang-cl.exe /O2 /LD /EHsc `
        "-I$pyInc" `
        "-I$vsDir\VC\Tools\MSVC\$msvcVer\include" `
        "-I$sdkRoot\Include\$sdkVer\ucrt" `
        "-I$sdkRoot\Include\$sdkVer\shared" `
        "-I$sdkRoot\Include\$sdkVer\um" `
        "$mod.c" /Fe:"$mod.pyd" `
        /link `
        "/LIBPATH:$pyLibDir" `
        "/LIBPATH:$vsDir\VC\Tools\MSVC\$msvcVer\lib\x64" `
        "/LIBPATH:$sdkRoot\Lib\$sdkVer\ucrt\x64" `
        "/LIBPATH:$sdkRoot\Lib\$sdkVer\um\x64"
    if ($LASTEXITCODE -ne 0) { throw "clang-cl compile failed for $mod" }
    Write-Host "✔ Built $mod.pyd" -ForegroundColor Green
}
