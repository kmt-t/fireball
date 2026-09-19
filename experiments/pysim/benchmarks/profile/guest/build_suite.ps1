# Builds suite.c into suite.wasm (wasm32 MVP, freestanding, no imports).
# Requires clang with the wasm32 target and wasm-ld on PATH.  The generated suite.wasm is
# committed (like aobench.wasm) so that running the workload does not need a toolchain.
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $dir "suite.c"
$out = Join-Path $dir "suite.wasm"

# -mcpu=mvp keeps the output inside pysim's MVP-only instruction set ({Wasm32Only}).
& clang --target=wasm32 -mcpu=mvp -O2 -ffreestanding -fno-builtin -nostdlib -Wall -Wextra `
    "-Wl,--no-entry" "-Wl,--strip-all" "-Wl,-z,stack-size=4096" `
    "-Wl,--initial-memory=65536" "-Wl,--max-memory=65536" `
    -o $out $src
if ($LASTEXITCODE -ne 0) { throw "clang failed" }
Write-Host ("Built {0} ({1} bytes)" -f $out, (Get-Item $out).Length)
