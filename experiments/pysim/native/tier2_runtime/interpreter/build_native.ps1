# Build the required Tier 2 native Interpreter (Windows / clang-cl).
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$projectRoot = (Resolve-Path (Join-Path $scriptDir "..\..\..\..\..")).Path

$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products '*' -property installationPath
if (-not $vsDir) { throw "Visual Studio Build Tools not found." }
$msvcVer = Get-ChildItem "$vsDir\VC\Tools\MSVC" | Select-Object -First 1 -ExpandProperty Name
$sdkRoot = "C:\Program Files (x86)\Windows Kits\10"
$sdkVer = Get-ChildItem "$sdkRoot\Include" | Select-Object -Last 1 -ExpandProperty Name
$nativeBuildDir = Join-Path $env:TEMP "fireball-pysim-native"
New-Item -ItemType Directory -Force -Path $nativeBuildDir | Out-Null
$sourceCxx = Join-Path $scriptDir "native_interpreter.cxx"
$pythonPackageDir = Join-Path $projectRoot "experiments\pysim\tier2_runtime\interpreter"
$outputDll = Join-Path $nativeBuildDir "native_interpreter.dll"

Write-Host ">>> Compiling native_interpreter.cxx -> native_interpreter.dll" -ForegroundColor Yellow
& clang-cl.exe /TP /std:c++latest /O2 /LD /W4 `
    "-I$scriptDir" `
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
Copy-Item -Force $outputDll (Join-Path $pythonPackageDir "native_interpreter.dll")
Write-Host "✔ Built native_interpreter.dll" -ForegroundColor Green
