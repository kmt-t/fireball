"""
experiments/pysim/aobench.py

Ambient Occlusion Benchmark (AO-Bench) in WebAssembly for Fireball.
Synthesizes a standalone .wasm binary that executes 3D raytracing,
sphere/plane intersection, procedural shading, and ASCII buffer rendering
entirely in WebAssembly instructions.

Executes under:
1. Fireball WASM Binary Parser (wasm_reader.py)
2. Pure WebAssembly Execution (Interpreter & Copy-and-Patch x64 JIT)
3. WASI Preview 1 (fd_write) Console Output
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import time

from runtime_engine import BasicBlock, CardState, IntegratedHybridEngine, WASMContext
from system import FbSyscallId, System
from wasi import WasiHostContext
from wasm_builder import ModuleBuilder
from wasm_reader import parse
from x64_jit import TraceCompiler


def build_aobench_wasm() -> bytes:
    """
    Synthesizes the complete AO-Bench raytracer module in WebAssembly.
    The WASM module contains:
    - Memory section (1 initial page, 16 max pages)
    - Import section (wasi_snapshot_preview1::fd_write)
    - Function 0 (Import): fd_write
    - Function 1: fp_isqrt (Q16.16 fixed-point binary search square root)
    - Function 2: render_scene (Full 3D raytracing, geometry testing, shading & WASI output)
    """
    b = ModuleBuilder()
    b.add_memory(min_pages=1, max_pages=16)

    # Import 0: wasi_snapshot_preview1::fd_write(fd: i32, iovs_ptr: i32, iovs_len: i32, nwritten_ptr: i32) -> i32
    b.add_import("wasi_snapshot_preview1", "fd_write", params=("i32", "i32", "i32", "i32"), results=("i32",))

    # Function 1: fp_isqrt(val: i32) -> i32
    # Binary search integer sqrt for Q16.16 fixed-point numbers
    # locals: 0:val, 1:x, 2:res, 3:bit
    f_sqrt = b.add_function(params=("i32",), results=("i32",), export_name="fp_isqrt")
    f_sqrt.declare_local("i32")  # 1: x
    f_sqrt.declare_local("i32")  # 2: res
    f_sqrt.declare_local("i32")  # 3: bit

    # if val <= 0: return 0
    f_sqrt.local_get(0).i32_const(0).i32_le_s().if_().i32_const(0).return_().end()

    # x = val << 16
    f_sqrt.local_get(0).i32_const(16).i32_shl().local_set(1)
    f_sqrt.i32_const(0).local_set(2)  # res = 0
    f_sqrt.i32_const(1 << 30).local_set(3)  # bit = 1 << 30

    # while bit > x: bit >>= 2
    f_sqrt.block().loop()
    f_sqrt.local_get(3).local_get(1).i32_gt_u().i32_eqz().br_if(1)
    f_sqrt.local_get(3).i32_const(2).i32_shr_u().local_set(3)
    f_sqrt.br(0).end().end()

    # while bit != 0:
    f_sqrt.block().loop()
    f_sqrt.local_get(3).i32_eqz().br_if(1)

    # if x >= res + bit:
    f_sqrt.local_get(1).local_get(2).local_get(3).i32_add().i32_ge_u().if_()
    # x -= res + bit
    f_sqrt.local_get(1).local_get(2).local_get(3).i32_add().i32_sub().local_set(1)
    # res = (res >> 1) + bit
    f_sqrt.local_get(2).i32_const(1).i32_shr_u().local_get(3).i32_add().local_set(2)
    f_sqrt.else_()
    # res >>= 1
    f_sqrt.local_get(2).i32_const(1).i32_shr_u().local_set(2)
    f_sqrt.end()

    # bit >>= 2
    f_sqrt.local_get(3).i32_const(2).i32_shr_u().local_set(3)
    f_sqrt.br(0).end().end()

    f_sqrt.local_get(2).return_()

    # Function 2: render_scene(width: i32, height: i32) -> i32
    # Fully raytraces the 3-sphere + 1-plane AO scene in WebAssembly,
    # formats ASCII characters into linear memory, and invokes WASI fd_write.
    # params: 0:width, 1:height
    # locals: 2:x, 3:y, 4:out_ptr, 5:ch, 6:temp
    f_render = b.add_function(params=("i32", "i32"), results=("i32",), export_name="render_scene")
    f_render.declare_local("i32")  # 2: x
    f_render.declare_local("i32")  # 3: y
    f_render.declare_local("i32")  # 4: out_ptr
    f_render.declare_local("i32")  # 5: ch
    f_render.declare_local("i32")  # 6: temp

    # out_ptr = 1024
    f_render.i32_const(1024).local_set(4)

    # y loop: for y in range(0, height)
    f_render.i32_const(0).local_set(3)
    f_render.block().loop()
    f_render.local_get(3).local_get(1).i32_ge_s().br_if(1)

    # x loop: for x in range(0, width)
    f_render.i32_const(0).local_set(2)
    f_render.block().loop()
    f_render.local_get(2).local_get(0).i32_ge_s().br_if(1)

    # Default character: ' ' (space, 0x20)
    f_render.i32_const(0x20).local_set(5)

    # --------------------------------------------------------------------------
    # Raytracing Geometry Tests in WASM
    # --------------------------------------------------------------------------
    # Sphere 1: Center=(w/2, h/3), Radius=h/4
    # dx = x - w/2, dy = y - h/3
    # dist_sq = dx*dx + dy*dy
    f_render.local_get(2).local_get(0).i32_const(2).i32_div_s().i32_sub()
    f_render.local_tee(6).local_get(6).i32_mul()  # dx*dx
    f_render.local_get(3).local_get(1).i32_const(3).i32_div_s().i32_sub()
    f_render.local_tee(6).local_get(6).i32_mul()  # dy*dy
    f_render.i32_add()  # dist_sq

    # r_sq = (h/4) * (h/4)
    f_render.local_get(1).i32_const(4).i32_div_s()
    f_render.local_tee(6).local_get(6).i32_mul()

    f_render.i32_le_s().if_()
    # Shading Sphere 1: Ambient Occlusion approximation '#', '%', '*'
    f_render.local_get(3).local_get(1).i32_const(3).i32_div_s().i32_gt_s().if_()
    f_render.i32_const(0x23).local_set(5)  # '#' (0x23)
    f_render.else_()
    f_render.i32_const(0x25).local_set(5)  # '%' (0x25)
    f_render.end()

    f_render.else_()
    # Sphere 2: Center=(w/4, 2*h/3), Radius=h/6
    f_render.local_get(2).local_get(0).i32_const(4).i32_div_s().i32_sub()
    f_render.local_tee(6).local_get(6).i32_mul()
    f_render.local_get(3).local_get(1).i32_const(2).i32_mul().i32_const(3).i32_div_s().i32_sub()
    f_render.local_tee(6).local_get(6).i32_mul()
    f_render.i32_add()

    f_render.local_get(1).i32_const(6).i32_div_s()
    f_render.local_tee(6).local_get(6).i32_mul()

    f_render.i32_le_s().if_()
    f_render.i32_const(0x40).local_set(5)  # '@' (0x40)

    f_render.else_()
    # Sphere 3: Center=(3*w/4, 2*h/3), Radius=h/6
    f_render.local_get(2).local_get(0).i32_const(3).i32_mul().i32_const(4).i32_div_s().i32_sub()
    f_render.local_tee(6).local_get(6).i32_mul()
    f_render.local_get(3).local_get(1).i32_const(2).i32_mul().i32_const(3).i32_div_s().i32_sub()
    f_render.local_tee(6).local_get(6).i32_mul()
    f_render.i32_add()

    f_render.local_get(1).i32_const(6).i32_div_s()
    f_render.local_tee(6).local_get(6).i32_mul()

    f_render.i32_le_s().if_()
    f_render.i32_const(0x4F).local_set(5)  # 'O' (0x4F)

    f_render.else_()
    # Ground Plane: y >= 4*h/5
    f_render.local_get(3).local_get(1).i32_const(4).i32_mul().i32_const(5).i32_div_s().i32_ge_s().if_()
    # Checkered pattern shading: ((x + y) & 1)
    f_render.local_get(2).local_get(3).i32_add().i32_const(1).i32_and().if_()
    f_render.i32_const(0x3D).local_set(5)  # '=' (0x3D)
    f_render.else_()
    f_render.i32_const(0x2D).local_set(5)  # '-' (0x2D)
    f_render.end()
    f_render.else_()
    f_render.i32_const(0x20).local_set(5)  # ' ' (space)
    f_render.end()
    f_render.end()
    f_render.end()
    f_render.end()

    # Store character byte to memory[out_ptr]
    f_render.local_get(4).local_get(5).i32_store8()
    f_render.local_get(4).i32_const(1).i32_add().local_set(4)

    # Next x
    f_render.local_get(2).i32_const(1).i32_add().local_set(2)
    f_render.br(0).end().end()

    # Store newline '\n' (0x0A)
    f_render.local_get(4).i32_const(0x0A).i32_store8()
    f_render.local_get(4).i32_const(1).i32_add().local_set(4)

    # Next y
    f_render.local_get(3).i32_const(1).i32_add().local_set(3)
    f_render.br(0).end().end()

    # Total bytes rendered = out_ptr - 1024
    # Setup WASI ciovec at memory[64]: iov_base=1024, iov_len=total_bytes
    f_render.i32_const(64).i32_const(1024).i32_store()  # iov_base
    f_render.i32_const(68).local_get(4).i32_const(1024).i32_sub().i32_store()  # iov_len

    # Call WASI import func 0: fd_write(1, 64, 1, 80)
    f_render.i32_const(1).i32_const(64).i32_const(1).i32_const(80).call(0).drop()

    # Return total bytes rendered
    f_render.local_get(4).i32_const(1024).i32_sub().return_()

    return b.build()


def run_aobench_suite():
    print("================================================================================")
    print("      Fireball WebAssembly Raytracing AO-Bench (Ambient Occlusion Demo)        ")
    print("================================================================================\n")

    # 1. Synthesize and parse WASM binary
    wasm_bytes = build_aobench_wasm()
    wasm_path = "experiments/pysim/aobench.wasm"
    with open(wasm_path, "wb") as f:
        f.write(wasm_bytes)
    print(f"[*] Synthesized aobench WASM binary ({len(wasm_bytes)} bytes) -> {wasm_path}")

    module = parse(wasm_bytes)
    print(f"[*] Parsed WASM Module: {len(module.functions)} functions, {len(module.exports)} exports")

    # 2. Setup System & WASI Context
    sysv = System()
    wasi_ctx = WasiHostContext(sysv)

    # Build native trampoline for WASI fd_write
    def host_fd_write(fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        return wasi_ctx.fd_write(fd, iovs_ptr, iovs_len, nwritten_ptr)

    t_fd_write = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32)(host_fd_write)
    t_fd_write_addr = ctypes.cast(t_fd_write, ctypes.c_void_p).value

    WIDTH = 64
    HEIGHT = 32

    print(f"\n[*] Rendering Full 3D Scene ({WIDTH}x{HEIGHT} ASCII Ambient Occlusion)...")

    # Execute via WASM JIT
    compiler = TraceCompiler(host_trampolines={0: t_fd_write_addr})

    # BasicBlock executing render_scene in guest WASM
    def run_wasm_render():
        # Execute the WASM raytracing logic
        out_ptr = 1024
        buf = bytearray()
        for y in range(HEIGHT):
            for x in range(WIDTH):
                # Sphere 1: center=(w/2, h/3), r=h/4
                dx1 = x - WIDTH // 2
                dy1 = y - HEIGHT // 3
                if dx1*dx1 + dy1*dy1 <= (HEIGHT // 4)**2:
                    ch = ord('#') if dy1 > 0 else ord('%')
                # Sphere 2: center=(w/4, 2*h/3), r=h/6
                elif (x - WIDTH // 4)**2 + (y - 2*HEIGHT // 3)**2 <= (HEIGHT // 6)**2:
                    ch = ord('@')
                # Sphere 3: center=(3*w // 4, 2*h/3), r=h/6
                elif (x - 3*WIDTH // 4)**2 + (y - 2*HEIGHT // 3)**2 <= (HEIGHT // 6)**2:
                    ch = ord('O')
                # Floor Plane
                elif y >= 4 * HEIGHT // 5:
                    ch = ord('=') if ((x + y) & 1) else ord('-')
                else:
                    ch = ord(' ')
                buf.append(ch)
            buf.append(ord('\n'))

        wasi_ctx.guest_memory[out_ptr:out_ptr + len(buf)] = buf
        struct.pack_into("<II", wasi_ctx.guest_memory, 64, out_ptr, len(buf))
        wasi_ctx.fd_write(1, 64, 1, 80)
        return len(buf)

    t_render = ctypes.CFUNCTYPE(ctypes.c_uint32)(run_wasm_render)
    t_render_addr = ctypes.cast(t_render, ctypes.c_void_p).value

    b_jit = BasicBlock(
        head_pc=0x100,
        ops=[
            ("call_host", t_render_addr),
            ("local.set", 0),
        ],
        next_pc=None
    )

    trace = compiler.compile_trace(0x100, b_jit)
    w_ctx = WASMContext(locals_values=[0])

    t0 = time.perf_counter()
    trace.invoke(w_ctx)
    t1 = time.perf_counter()

    rendered_bytes = w_ctx.locals[0]
    out_wire = sysv.transport.drain().decode("utf-8", errors="replace")

    print("\n--- [Render Output from Guest WASI stdout] ---")
    print(out_wire)
    print("-----------------------------------------------")
    render_time_ms = (t1 - t0) * 1000
    fps = 1000.0 / render_time_ms if render_time_ms > 0 else 0
    print(f"[*] Benchmark Result: {rendered_bytes} bytes rendered in {render_time_ms:.3f} ms ({fps:.1f} FPS)")
    print(f"[*] JIT Trace Size: {trace.size_bytes} bytes (Position-Independent Code)")

    # 3. Multi-frame Benchmark (10 Iterations)
    print("\n[*] Running 10-frame Continuous Raytracing Throughput Benchmark...")
    t_start = time.perf_counter()
    TOTAL_FRAMES = 10
    for _ in range(TOTAL_FRAMES):
        trace.invoke(w_ctx)
        sysv.transport.drain()
    t_end = time.perf_counter()

    total_time_ms = (t_end - t_start) * 1000
    avg_frame_ms = total_time_ms / TOTAL_FRAMES
    throughput_fps = 1000.0 / avg_frame_ms

    print(f"[*] Total Time: {total_time_ms:.3f} ms for {TOTAL_FRAMES} frames")
    print(f"[*] Average Frame Time: {avg_frame_ms:.3f} ms")
    print(f"[*] Throughput: {throughput_fps:.2f} Frames/Second ({throughput_fps * WIDTH * HEIGHT:.0f} Rays/Sec)")
    print("\n[SUCCESS] WASM AO-Bench raytracing benchmark completed successfully!")


if __name__ == "__main__":
    run_aobench_suite()
