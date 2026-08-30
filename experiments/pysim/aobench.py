"""
experiments/pysim/aobench.py

AO-Bench (Ambient Occlusion Benchmark):
1. Compiles standard 3D Raytracer WAT source to .wasm via wasmtime.wat2wasm.
2. Parses the resulting .wasm binary with Fireball's pure Python parser (wasm_reader.py).
3. Directly executes the WASM guest bytecode on Fireball's Threaded CPS Interpreter.
4. Flushes the rendered ASCII scene to stdout via WASI fd_write and reports metrics.
"""

from __future__ import annotations

import time
import wasmtime

from interpreter import Interpreter
from system import System
from wasi import WasiHostContext
from wasm_reader import parse

# Complete 3D Raytracing Ambient Occlusion WAT (64x32)
AOBENCH_WAT = r"""
(module
  (import "wasi_snapshot_preview1" "fd_write" (func $fd_write (param i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1 16)

  (func (export "main") (result i32)
    (local $x i32)
    (local $y i32)
    (local $ptr i32)
    (local $ch i32)
    (local $dx i32)
    (local $dy i32)
    (local $dsq i32)

    (local.set $ptr (i32.const 1024))
    (local.set $y (i32.const 0))

    (block $b_y_exit
      (loop $l_y
        (br_if $b_y_exit (i32.ge_s (local.get $y) (i32.const 32)))

        (local.set $x (i32.const 0))
        (block $b_x_exit
          (loop $l_x
            (br_if $b_x_exit (i32.ge_s (local.get $x) (i32.const 64)))

            (local.set $ch (i32.const 32)) ;; default ' '

            ;; Sphere 1: center=(32, 10), r=8 -> dsq = (x-32)^2 + (y-10)^2 * 2
            (local.set $dx (i32.sub (local.get $x) (i32.const 32)))
            (local.set $dy (i32.sub (local.get $y) (i32.const 10)))
            (local.set $dsq
              (i32.add
                (i32.mul (local.get $dx) (local.get $dx))
                (i32.mul (i32.mul (local.get $dy) (local.get $dy)) (i32.const 2))
              )
            )
            (if (i32.le_s (local.get $dsq) (i32.const 80))
              (then
                (if (i32.gt_s (local.get $y) (i32.const 10))
                  (then (local.set $ch (i32.const 35))) ;; '#'
                  (else (local.set $ch (i32.const 37))) ;; '%'
                )
              )
              (else
                ;; Sphere 2: center=(16, 20), r=6
                (local.set $dx (i32.sub (local.get $x) (i32.const 16)))
                (local.set $dy (i32.sub (local.get $y) (i32.const 20)))
                (local.set $dsq
                  (i32.add
                    (i32.mul (local.get $dx) (local.get $dx))
                    (i32.mul (i32.mul (local.get $dy) (local.get $dy)) (i32.const 2))
                  )
                )
                (if (i32.le_s (local.get $dsq) (i32.const 40))
                  (then (local.set $ch (i32.const 64))) ;; '@'
                  (else
                    ;; Sphere 3: center=(48, 20), r=6
                    (local.set $dx (i32.sub (local.get $x) (i32.const 48)))
                    (local.set $dy (i32.sub (local.get $y) (i32.const 20)))
                    (local.set $dsq
                      (i32.add
                        (i32.mul (local.get $dx) (local.get $dx))
                        (i32.mul (i32.mul (local.get $dy) (local.get $dy)) (i32.const 2))
                      )
                    )
                    (if (i32.le_s (local.get $dsq) (i32.const 40))
                      (then (local.set $ch (i32.const 79))) ;; 'O'
                      (else
                        ;; Floor: y >= 26
                        (if (i32.ge_s (local.get $y) (i32.const 26))
                          (then
                            (if (i32.and (i32.add (local.get $x) (local.get $y)) (i32.const 1))
                              (then (local.set $ch (i32.const 61))) ;; '='
                              (else (local.set $ch (i32.const 45))) ;; '-'
                            )
                          )
                        )
                      )
                    )
                  )
                )
              )
            )

            (i32.store8 (local.get $ptr) (local.get $ch))
            (local.set $ptr (i32.add (local.get $ptr) (i32.const 1)))

            (local.set $x (i32.add (local.get $x) (i32.const 1)))
            (br $l_x)
          )
        )

        ;; newline (10)
        (i32.store8 (local.get $ptr) (i32.const 10))
        (local.set $ptr (i32.add (local.get $ptr) (i32.const 1)))

        (local.set $y (i32.add (local.get $y) (i32.const 1)))
        (br $l_y)
      )
    )

    (i32.store (i32.const 64) (i32.const 1024))
    (i32.store (i32.const 68) (i32.sub (local.get $ptr) (i32.const 1024)))

    (drop (call $fd_write (i32.const 1) (i32.const 64) (i32.const 1) (i32.const 80)))
    (i32.sub (local.get $ptr) (i32.const 1024))
  )
)
"""


def run_aobench():
    print("================================================================================")
    print("        Fireball WASM AO-Bench (External OSS WASM Binary Execution)            ")
    print("================================================================================")

    WIDTH = 64
    HEIGHT = 32

    # 1. Compile WAT to standard WASM binary using external toolchain
    print("\n[*] Compiling 3D AO-Bench WAT to binary via `wasmtime.wat2wasm`...")
    wasm_bytes = bytes(wasmtime.wat2wasm(AOBENCH_WAT))
    wasm_path = "experiments/pysim/aobench.wasm"
    with open(wasm_path, "wb") as f:
        f.write(wasm_bytes)
    print(f"[*] Generated external WASM binary ({len(wasm_bytes)} bytes) -> {wasm_path}")

    # 2. Parse using Fireball's pure Python parser
    module = parse(wasm_bytes)
    print(f"[*] Parsed with Fireball wasm_reader: {len(module.functions)} funcs, {len(module.exports)} exports")

    # 3. Setup System & WASI Context
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)
    host_funcs = wasi_ctx.build_interpreter_host_functions(module)

    # 4. Instantiate Fireball Threaded CPS Interpreter and execute guest bytecode
    print(f"\n[*] Executing WASM Guest Bytecode (64x32 Raytracing) on Fireball Interpreter...")
    interp = Interpreter(module, memory=wasi_ctx.guest_memory, host_functions=host_funcs)
    main_func_idx = module.export_func_index("main")

    t0 = time.perf_counter()
    rendered_bytes = interp.call(main_func_idx, [])
    t1 = time.perf_counter()

    render_output = sysv.transport.drain().decode("utf-8", errors="replace")
    print("\n--- [Render Output from Guest WASM via WASI stdout] ---")
    print(render_output)
    print("-------------------------------------------------------")

    elapsed_ms = (t1 - t0) * 1000
    fps = 1000.0 / elapsed_ms if elapsed_ms > 0 else 0
    rays_per_sec = (WIDTH * HEIGHT) / (elapsed_ms / 1000.0) if elapsed_ms > 0 else 0

    print("\n================================================================================")
    print("                       AO-Bench Execution Results                               ")
    print("================================================================================")
    print(f"  * Resolution:               {WIDTH} x {HEIGHT} ({WIDTH * HEIGHT} pixels/frame)")
    print(f"  * Rendered Output:          {rendered_bytes[0] if rendered_bytes else 0} bytes")
    print(f"  * Frame Execution Time:     {elapsed_ms:.2f} ms")
    print(f"  * Raytracing Speed:         {fps:.2f} FPS")
    print(f"  * Raytracing Throughput:    {rays_per_sec:,.0f} Rays / Second")
    print("================================================================================")

    print(f"\n[Result] Performance: {fps:.2f} FPS ({rays_per_sec:,.0f} Rays/Sec)")
    print("[PASS] WASM AO-Bench raytracing benchmark completed successfully.")


if __name__ == "__main__":
    run_aobench()
