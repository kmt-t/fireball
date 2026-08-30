"""
experiments/pysim/aobench.py

Genuine 3D Ambient Occlusion Benchmark (AO-Bench):
1. Written in standard WAT using fixed-point Q8.8 arithmetic (no floats, {Wasm32Only} compliant).
2. Computes:
   - Primary ray generation per pixel (Origin O, Direction D).
   - Ray-Sphere (quadratic discriminant) and Ray-Plane 3D intersections.
   - Hit point P and 4 hemisphere sample rays per hit for Ambient Occlusion shading.
   - Occlusion integration and ASCII gradation mapping.
3. Compiled to .wasm binary using OSS wasmtime.wat2wasm.
4. Directly parsed with Fireball wasm_reader.py and executed on Tier 2 Threaded CPS Interpreter.
5. Flushes rendered ASCII output via WASI fd_write and reports exact ray count & throughput.
"""

from __future__ import annotations

import time
import wasmtime

from interpreter import Interpreter
from system import System
from wasi import WasiHostContext
from wasm_reader import parse

# ---------------------------------------------------------------------------
# Genuine 3D Raytracing Ambient Occlusion WAT (Fixed-point Q8.8: 1.0 = 256)
# ---------------------------------------------------------------------------
GENUINE_AO_WAT = r"""
(module
  (import "wasi_snapshot_preview1" "fd_write" (func $fd_write (param i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1 16)

  ;; Fixed-point Q8.8 arithmetic: fp_mul(a, b) = (a * b) >> 8
  (func $fp_mul (param $a i32) (param $b i32) (result i32)
    (i32.shr_s (i32.mul (local.get $a) (local.get $b)) (i32.const 8))
  )

  ;; Fixed-point Q8.8 division: fp_div(a, b) = (a << 8) / b
  (func $fp_div (param $a i32) (param $b i32) (result i32)
    (if (i32.eqz (local.get $b))
      (then (return (i32.const 0)))
    )
    (i32.div_s (i32.shl (local.get $a) (i32.const 8)) (local.get $b))
  )

  ;; Fixed-point Q8.8 square root using binary shift integer method
  (func $fp_sqrt (param $x i32) (result i32)
    (local $val i32)
    (local $res i32)
    (local $bit i32)
    (if (i32.le_s (local.get $x) (i32.const 0))
      (then (return (i32.const 0)))
    )
    (local.set $val (i32.shl (local.get $x) (i32.const 8)))
    (local.set $res (i32.const 0))
    (local.set $bit (i32.const 1073741824))
    (block $b0
      (loop $l0
        (br_if $b0 (i32.eqz (i32.gt_u (local.get $bit) (local.get $val))))
        (local.set $bit (i32.shr_u (local.get $bit) (i32.const 2)))
        (br $l0)
      )
    )
    (block $b1
      (loop $l1
        (br_if $b1 (i32.eqz (local.get $bit)))
        (if (i32.ge_u (local.get $val) (i32.add (local.get $res) (local.get $bit)))
          (then
            (local.set $val (i32.sub (local.get $val) (i32.add (local.get $res) (local.get $bit))))
            (local.set $res (i32.add (i32.shr_u (local.get $res) (i32.const 1)) (local.get $bit)))
          )
          (else
            (local.set $res (i32.shr_u (local.get $res) (i32.const 1)))
          )
        )
        (local.set $bit (i32.shr_u (local.get $bit) (i32.const 2)))
        (br $l1)
      )
    )
    (local.get $res)
  )

  ;; Ray-Sphere 3D intersection (quadratic discriminant: b^2 - c)
  (func $ray_sphere (param $ox i32) (param $oy i32) (param $oz i32)
                    (param $dx i32) (param $dy i32) (param $dz i32)
                    (param $cx i32) (param $cy i32) (param $cz i32)
                    (param $rsq i32) (result i32)
    (local $vx i32) (local $vy i32) (local $vz i32)
    (local $b i32) (local $c i32) (local $disc i32) (local $sq i32) (local $t i32)

    (local.set $vx (i32.sub (local.get $ox) (local.get $cx)))
    (local.set $vy (i32.sub (local.get $oy) (local.get $cy)))
    (local.set $vz (i32.sub (local.get $oz) (local.get $cz)))

    (local.set $b
      (i32.add
        (call $fp_mul (local.get $vx) (local.get $dx))
        (i32.add
          (call $fp_mul (local.get $vy) (local.get $dy))
          (call $fp_mul (local.get $vz) (local.get $dz))
        )
      )
    )

    (local.set $c
      (i32.sub
        (i32.add
          (call $fp_mul (local.get $vx) (local.get $vx))
          (i32.add
            (call $fp_mul (local.get $vy) (local.get $vy))
            (call $fp_mul (local.get $vz) (local.get $vz))
          )
        )
        (local.get $rsq)
      )
    )

    (local.set $disc (i32.sub (call $fp_mul (local.get $b) (local.get $b)) (local.get $c)))
    (if (i32.lt_s (local.get $disc) (i32.const 0))
      (then (return (i32.const -1)))
    )

    (local.set $sq (call $fp_sqrt (local.get $disc)))
    (local.set $t (i32.sub (i32.sub (i32.const 0) (local.get $b)) (local.get $sq)))
    (if (i32.gt_s (local.get $t) (i32.const 2))
      (then (return (local.get $t)))
    )
    (i32.const -1)
  )

  ;; Intersect whole 3D scene (3 Spheres + 1 Floor Plane): returns shortest t (>0) or -1
  (func $intersect_scene (param $ox i32) (param $oy i32) (param $oz i32)
                         (param $dx i32) (param $dy i32) (param $dz i32) (result i32)
    (local $tmin i32)
    (local $t i32)

    (local.set $tmin (i32.const 2147483647))

    ;; Sphere 1: center=(0, -102, 768), rsq=64 (radius=0.5)
    (local.set $t (call $ray_sphere (local.get $ox) (local.get $oy) (local.get $oz)
                                    (local.get $dx) (local.get $dy) (local.get $dz)
                                    (i32.const 0) (i32.const -102) (i32.const 768) (i32.const 64)))
    (if (i32.and (i32.gt_s (local.get $t) (i32.const 0)) (i32.lt_s (local.get $t) (local.get $tmin)))
      (then (local.set $tmin (local.get $t)))
    )

    ;; Sphere 2: center=(-230, 51, 845), rsq=41 (radius=0.4)
    (local.set $t (call $ray_sphere (local.get $ox) (local.get $oy) (local.get $oz)
                                    (local.get $dx) (local.get $dy) (local.get $dz)
                                    (i32.const -230) (i32.const 51) (i32.const 845) (i32.const 41)))
    (if (i32.and (i32.gt_s (local.get $t) (i32.const 0)) (i32.lt_s (local.get $t) (local.get $tmin)))
      (then (local.set $tmin (local.get $t)))
    )

    ;; Sphere 3: center=(230, 51, 845), rsq=41 (radius=0.4)
    (local.set $t (call $ray_sphere (local.get $ox) (local.get $oy) (local.get $oz)
                                    (local.get $dx) (local.get $dy) (local.get $dz)
                                    (i32.const 230) (i32.const 51) (i32.const 845) (i32.const 41)))
    (if (i32.and (i32.gt_s (local.get $t) (i32.const 0)) (i32.lt_s (local.get $t) (local.get $tmin)))
      (then (local.set $tmin (local.get $t)))
    )

    ;; Floor plane: y = 154 (y=0.6)
    (if (i32.gt_s (local.get $dy) (i32.const 12))
      (then
        (local.set $t (call $fp_div (i32.sub (i32.const 154) (local.get $oy)) (local.get $dy)))
        (if (i32.and (i32.gt_s (local.get $t) (i32.const 2)) (i32.lt_s (local.get $t) (local.get $tmin)))
          (then (local.set $tmin (local.get $t)))
        )
      )
    )

    (if (i32.lt_s (local.get $tmin) (i32.const 2147483647))
      (then (return (local.get $tmin)))
    )
    (i32.const -1)
  )

  ;; Main render routine: 3D Raytracing with 4 Ambient Occlusion sample rays per hit
  (func (export "main") (param $w i32) (param $h i32) (result i32)
    (local $x i32) (local $y i32) (local $ptr i32)
    (local $rdx i32) (local $rdy i32) (local $rdz i32) (local $rlen i32) (local $lensq i32)
    (local $thit i32) (local $px i32) (local $py i32) (local $pz i32)
    (local $unocc i32) (local $ch i32)

    (local.set $ptr (i32.const 1024))
    (local.set $y (i32.const 0))

    (block $b_y_exit
      (loop $l_y
        (br_if $b_y_exit (i32.ge_s (local.get $y) (local.get $h)))

        (local.set $x (i32.const 0))
        (block $b_x_exit
          (loop $l_x
            (br_if $b_x_exit (i32.ge_s (local.get $x) (local.get $w)))

            ;; Primary ray direction:
            ;; rdx = (x - w/2) * 256 / (w/2)
            (local.set $rdx (call $fp_div (i32.sub (local.get $x) (i32.shr_s (local.get $w) (i32.const 1)))
                                          (i32.shr_s (local.get $w) (i32.const 1))))
            ;; rdy = (y - h/2) * 384 / (h/2)
            (local.set $rdy (call $fp_div (i32.mul (i32.sub (local.get $y) (i32.shr_s (local.get $h) (i32.const 1))) (i32.const 384))
                                          (i32.mul (i32.shr_s (local.get $h) (i32.const 1)) (i32.const 256))))
            ;; rdz = 2.0 (512 in Q8.8)
            (local.set $rdz (i32.const 512))

            ;; Normalize primary ray
            (local.set $lensq
              (i32.add (call $fp_mul (local.get $rdx) (local.get $rdx))
              (i32.add (call $fp_mul (local.get $rdy) (local.get $rdy))
                       (call $fp_mul (local.get $rdz) (local.get $rdz)))))
            (local.set $rlen (call $fp_sqrt (local.get $lensq)))
            (local.set $rdx (call $fp_div (local.get $rdx) (local.get $rlen)))
            (local.set $rdy (call $fp_div (local.get $rdy) (local.get $rlen)))
            (local.set $rdz (call $fp_div (local.get $rdz) (local.get $rlen)))

            ;; Intersect primary ray
            (local.set $thit (call $intersect_scene (i32.const 0) (i32.const 0) (i32.const 0)
                                                   (local.get $rdx) (local.get $rdy) (local.get $rdz)))

            (if (i32.lt_s (local.get $thit) (i32.const 0))
              (then
                ;; Miss -> Space (32)
                (i32.store8 (local.get $ptr) (i32.const 32))
              )
              (else
                ;; Hit point P = t * D
                (local.set $px (call $fp_mul (local.get $thit) (local.get $rdx)))
                (local.set $py (call $fp_mul (local.get $thit) (local.get $rdy)))
                (local.set $pz (call $fp_mul (local.get $thit) (local.get $rdz)))

                ;; Shoot 4 AO sample rays from hit point
                (local.set $unocc (i32.const 0))

                ;; Sample 1: dir = (0, -204, 153)
                (if (i32.lt_s (call $intersect_scene (local.get $px) (i32.sub (local.get $py) (i32.const 10)) (i32.add (local.get $pz) (i32.const 10))
                                                     (i32.const 0) (i32.const -204) (i32.const 153)) (i32.const 0))
                  (then (local.set $unocc (i32.add (local.get $unocc) (i32.const 1))))
                )

                ;; Sample 2: dir = (153, -204, 0)
                (if (i32.lt_s (call $intersect_scene (i32.add (local.get $px) (i32.const 10)) (i32.sub (local.get $py) (i32.const 10)) (local.get $pz)
                                                     (i32.const 153) (i32.const -204) (i32.const 0)) (i32.const 0))
                  (then (local.set $unocc (i32.add (local.get $unocc) (i32.const 1))))
                )

                ;; Sample 3: dir = (-153, -204, 0)
                (if (i32.lt_s (call $intersect_scene (i32.sub (local.get $px) (i32.const 10)) (i32.sub (local.get $py) (i32.const 10)) (local.get $pz)
                                                     (i32.const -153) (i32.const -204) (i32.const 0)) (i32.const 0))
                  (then (local.set $unocc (i32.add (local.get $unocc) (i32.const 1))))
                )

                ;; Sample 4: dir = (0, -204, -153)
                (if (i32.lt_s (call $intersect_scene (local.get $px) (i32.sub (local.get $py) (i32.const 10)) (i32.sub (local.get $pz) (i32.const 10))
                                                     (i32.const 0) (i32.const -204) (i32.const -153)) (i32.const 0))
                  (then (local.set $unocc (i32.add (local.get $unocc) (i32.const 1))))
                )

                ;; Map unoccluded count (0..4) to ASCII shading character:
                ;; 0: '.', 1: ':', 2: '+', 3: '#', 4: '@'
                (local.set $ch (i32.const 64)) ;; '@'
                (if (i32.eq (local.get $unocc) (i32.const 0)) (then (local.set $ch (i32.const 46)))) ;; '.'
                (if (i32.eq (local.get $unocc) (i32.const 1)) (then (local.set $ch (i32.const 58)))) ;; ':'
                (if (i32.eq (local.get $unocc) (i32.const 2)) (then (local.set $ch (i32.const 43)))) ;; '+'
                (if (i32.eq (local.get $unocc) (i32.const 3)) (then (local.set $ch (i32.const 35)))) ;; '#'

                (i32.store8 (local.get $ptr) (local.get $ch))
              )
            )

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
    print("      Fireball 3D Ambient Occlusion Benchmark (Genuine WASM Execution)         ")
    print("================================================================================")

    WIDTH = 32
    HEIGHT = 16
    AO_SAMPLES = 4

    # 1. Compile 3D AO-Bench WAT to standard WASM binary using external toolchain
    print("\n[*] Step 1: Compiling Q8.8 3D AO-Bench WAT via `wasmtime.wat2wasm`...")
    wasm_bytes = bytes(wasmtime.wat2wasm(GENUINE_AO_WAT))
    wasm_path = "experiments/pysim/aobench.wasm"
    with open(wasm_path, "wb") as f:
        f.write(wasm_bytes)
    print(f"    -> Generated external WASM binary ({len(wasm_bytes)} bytes) -> {wasm_path}")

    # 2. Parse using Fireball's pure Python parser
    print("\n[*] Step 2: Parsing binary with Fireball wasm_reader...")
    module = parse(wasm_bytes)
    print(f"    -> Parsed Module: {len(module.functions)} functions, {len(module.exports)} exports")

    # 3. Setup System & WASI Context
    from runtime_engine import RuntimeEngine
    from x64_jit import TraceCompiler

    # 3. Setup System & WASI Context for Tier 2 Baseline
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)
    host_funcs = wasi_ctx.build_interpreter_host_functions(module)

    # 4. Tier 2: Pure Threaded CPS Interpreter Execution
    print(f"\n[*] Step 3: Executing on Tier 2 Threaded CPS Interpreter ({WIDTH}x{HEIGHT}, {AO_SAMPLES} samples/hit)...")
    interp_t2 = Interpreter(module, memory=wasi_ctx.guest_memory, host_functions=host_funcs)
    main_func_idx = module.export_func_index("main")

    t0_t2 = time.perf_counter()
    rendered_bytes = interp_t2.call(main_func_idx, [WIDTH, HEIGHT])
    t1_t2 = time.perf_counter()

    render_output = sysv.transport.drain().decode("utf-8", errors="replace")
    print("\n--- [Render Output from Guest WASM via WASI stdout] ---")
    print(render_output)
    print("-------------------------------------------------------")

    t2_time_ms = (t1_t2 - t0_t2) * 1000

    # Calculate actual ray count
    hit_pixels = sum(1 for ch in render_output if ch in ('.', ':', '+', '#', '@'))
    total_rays = (WIDTH * HEIGHT) + (hit_pixels * AO_SAMPLES)
    t2_rays_per_sec = total_rays / (t2_time_ms / 1000.0) if t2_time_ms > 0 else 0

    # 5. Tier 3: Integrated Hybrid Execution with 2-bit Card Marking & idle_hook JIT Compilation
    print(f"[*] Step 4: Executing on Tier 3 RuntimeEngine (Card-Marking Hotspot Profiler + idle_hook JIT Compiler)...")
    sysv_t3 = System()
    wasi_ctx_t3 = WasiHostContext(sysv_t3)
    host_funcs_t3 = wasi_ctx_t3.build_interpreter_host_functions(module)

    trace_compiler = TraceCompiler()
    runtime_engine = RuntimeEngine(jit_compiler=trace_compiler, yield_threshold=16)
    runtime_engine.register_module_blocks(module)

    interp_t3 = Interpreter(module, memory=wasi_ctx_t3.guest_memory, host_functions=host_funcs_t3, runtime_engine=runtime_engine)

    # Run cooperatively on COOS scheduler, draining compile queue via idle_hook on yields
    t0_t3 = time.perf_counter()
    coro = interp_t3.call_coroutine(main_func_idx, [WIDTH, HEIGHT], yield_every=32)
    try:
        while True:
            next(coro)
            # COOS idle_hook: drain compile queue and batch-compile hot basic blocks
            runtime_engine.idle_hook(budget=4)
    except StopIteration:
        pass
    t1_t3 = time.perf_counter()

    render_output_t3 = sysv_t3.transport.drain().decode("utf-8", errors="replace")
    t3_time_ms = (t1_t3 - t0_t3) * 1000
    t3_rays_per_sec = total_rays / (t3_time_ms / 1000.0) if t3_time_ms > 0 else 0
    speedup_ratio = t2_time_ms / t3_time_ms if t3_time_ms > 0 else 1.0

    # Differential Verification: verify byte-for-byte exact equality between Tier 2 and Tier 3 outputs
    is_identical = (render_output == render_output_t3)
    has_no_nul = ('\x00' not in render_output)
    expected_bytes = (WIDTH + 1) * HEIGHT  # (32 chars + 1 newline) * 16 rows = 528 bytes
    is_valid_size = (len(render_output.encode("utf-8")) == expected_bytes)

    assert is_identical, "CRITICAL: Tier 3 JIT output diverges from Tier 2 Interpreter reference output!"
    assert has_no_nul, "CRITICAL: Output contains corrupted NUL bytes!"
    assert is_valid_size, f"CRITICAL: Output size {len(render_output.encode('utf-8'))} != expected {expected_bytes} bytes!"

    print("\n================================================================================")
    print("                     3D AO-Bench Performance Results (Genuine Measured)         ")
    print("================================================================================")
    print(f"  * Resolution:               {WIDTH} x {HEIGHT} ({WIDTH * HEIGHT} primary rays)")
    print(f"  * Hit Pixels:               {hit_pixels} ({hit_pixels * AO_SAMPLES} AO sample rays)")
    print(f"  * Total Rays Traced:        {total_rays:,} Rays / Frame")
    print(f"  * Output Verified:          {len(render_output.encode('utf-8'))} bytes (Exact match: 33 B x 16 rows, 0 NULs)")
    print(f"  * Differential Check:       PASS (Tier 2 & Tier 3 match byte-for-byte)")
    print("--------------------------------------------------------------------------------")
    print(f"  * Tier 2 (Threaded CPS):    {t2_time_ms:.2f} ms / frame  ({t2_rays_per_sec:,.0f} Rays / Sec)")
    print(f"  * Tier 3 (Hybrid + JIT):    {t3_time_ms:.2f} ms / frame  ({t3_rays_per_sec:,.0f} Rays / Sec)")
    print(f"  * Measured Speedup Ratio:   {speedup_ratio:.2f}x faster")
    print(f"  * JIT Traces Compiled:      {len(runtime_engine.cache.active.traces)} traces in Active cache bank")
    print("================================================================================")

    print(f"\n[Result] Genuine 3D AO-Bench: {total_rays:,} Rays traced in {t2_time_ms:.2f} ms (Tier 2) vs {t3_time_ms:.2f} ms (Tier 3), Speedup: {speedup_ratio:.2f}x.")
    print("[PASS] 3D Ambient Occlusion differential verification & benchmark completed successfully.")


if __name__ == "__main__":
    run_aobench()
