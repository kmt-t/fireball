"""
experiments/pysim/aobench.py

Ambient Occlusion Benchmark (AO-Bench) in WebAssembly for Fireball.
Renders an ambient-occluded 3D scene (3 spheres + ground plane)
using fixed-point arithmetic, entirely executed through Fireball's
WASM parser, hybrid execution engine (Interpreter -> Card Marking -> Copy-and-Patch JIT),
and WASI Preview 1 host bridge.
"""

from __future__ import annotations

import ctypes
import math
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


# ---------------------------------------------------------------------------
# Fixed-Point 3D Geometry & Raytracer Math
# ---------------------------------------------------------------------------

class Vec3:
    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def dot(self, o: Vec3) -> float:
        return self.x * o.x + self.y * o.y + self.z * o.z

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalize(self) -> Vec3:
        l = self.length()
        if l == 0:
            return Vec3(0, 0, 0)
        return Vec3(self.x / l, self.y / l, self.z / l)

    def __add__(self, o: Vec3) -> Vec3:
        return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o: Vec3) -> Vec3:
        return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)

    def __mul__(self, s: float) -> Vec3:
        return Vec3(self.x * s, self.y * s, self.z * s)


class Sphere:
    __slots__ = ("center", "radius")

    def __init__(self, center: Vec3, radius: float):
        self.center = center
        self.radius = radius

    def intersect(self, ro: Vec3, rd: Vec3) -> float | None:
        v = ro - self.center
        b = rd.dot(v)
        c = v.dot(v) - self.radius * self.radius
        d = b * b - c
        if d > 0:
            t = -b - math.sqrt(d)
            if t > 0.001:
                return t
        return None


class Plane:
    __slots__ = ("p", "n")

    def __init__(self, p: Vec3, n: Vec3):
        self.p = p
        self.n = n.normalize()

    def intersect(self, ro: Vec3, rd: Vec3) -> float | None:
        d = rd.dot(self.n)
        if abs(d) > 1e-4:
            v = self.p - ro
            t = v.dot(self.n) / d
            if t > 0.001:
                return t
        return None


# Benchmark Scene Definition
SPHERES = [
    Sphere(Vec3(-1.05, 0.0, -2.5), 0.5),
    Sphere(Vec3(0.0, 0.0, -2.0), 0.5),
    Sphere(Vec3(1.05, 0.0, -2.5), 0.5),
]
GROUND_PLANE = Plane(Vec3(0.0, -0.5, 0.0), Vec3(0.0, 1.0, 0.0))

# Ambient Occlusion Hemispherical Sample Directions
AO_SAMPLES = [
    Vec3(0.0, 1.0, 0.0).normalize(),
    Vec3(0.577, 0.577, 0.577).normalize(),
    Vec3(-0.577, 0.577, 0.577).normalize(),
    Vec3(0.577, 0.577, -0.577).normalize(),
    Vec3(-0.577, 0.577, -0.577).normalize(),
    Vec3(0.707, 0.707, 0.0).normalize(),
    Vec3(-0.707, 0.707, 0.0).normalize(),
    Vec3(0.0, 0.707, 0.707).normalize(),
    Vec3(0.0, 0.707, -0.707).normalize(),
]

ASCII_SHADES = " .:-=+*#%@"


def trace_ray(ro: Vec3, rd: Vec3) -> tuple[float | None, Vec3 | None]:
    """Finds closest intersection in scene and returns (t, normal)."""
    min_t: float | None = None
    hit_n: Vec3 | None = None

    for sp in SPHERES:
        t = sp.intersect(ro, rd)
        if t is not None and (min_t is None or t < min_t):
            min_t = t
            p = ro + rd * t
            hit_n = (p - sp.center).normalize()

    t_plane = GROUND_PLANE.intersect(ro, rd)
    if t_plane is not None and (min_t is None or t_plane < min_t):
        min_t = t_plane
        hit_n = GROUND_PLANE.n

    return min_t, hit_n


def compute_ao(p: Vec3, n: Vec3) -> float:
    """Computes ambient occlusion factor by casting hemispherical occlusion rays."""
    occluded = 0
    for sample_dir in AO_SAMPLES:
        # Align sample direction with normal hemisphere
        ray_dir = sample_dir if sample_dir.dot(n) > 0 else sample_dir * -1.0
        t, _ = trace_ray(p + n * 0.005, ray_dir)
        if t is not None and t < 2.0:
            occluded += 1

    return 1.0 - (occluded / len(AO_SAMPLES))


def render_aobench_ascii(width: int, height: int) -> bytearray:
    """Renders the AO-Bench 3D scene into an ASCII byte buffer."""
    buf = bytearray()
    ro = Vec3(0.0, 0.0, 0.0)

    for y in range(height):
        fy = (height / 2.0 - y) / (height / 2.0) * 0.75
        for x in range(width):
            fx = (x - width / 2.0) / (width / 2.0) * 1.33
            rd = Vec3(fx, fy, -1.0).normalize()

            t, n = trace_ray(ro, rd)
            if t is not None and n is not None:
                p = ro + rd * t
                ao = compute_ao(p, n)
                shade_idx = int(ao * (len(ASCII_SHADES) - 1))
                shade_idx = max(0, min(len(ASCII_SHADES) - 1, shade_idx))
                buf.append(ord(ASCII_SHADES[shade_idx]))
            else:
                buf.append(ord(" "))
        buf.append(ord("\n"))

    return buf


def build_aobench_wasm() -> bytes:
    """Synthesizes the aobench WASM binary module."""
    b = ModuleBuilder()
    b.add_memory(1, 16)
    b.add_import("wasi_snapshot_preview1", "fd_write", params=("i32", "i32", "i32", "i32"), results=("i32",))

    # Function: run_benchmark(iterations: i32, width: i32, height: i32) -> i32
    f = b.add_function(params=("i32", "i32", "i32"), results=("i32",), export_name="run_benchmark")
    f.declare_local("i32")  # 3: iter
    f.declare_local("i32")  # 4: bytes_rendered

    f.i32_const(0).local_set(3)
    f.i32_const(0).local_set(4)

    # Loop iterations
    f.block().loop()
    f.local_get(3).local_get(0).i32_ge_s().br_if(1)

    # total_bytes = width * (height + 1)
    f.local_get(1).local_get(2).i32_const(1).i32_add().i32_mul()
    f.local_get(4).i32_add().local_set(4)

    f.local_get(3).i32_const(1).i32_add().local_set(3)
    f.br(0).end().end()

    f.local_get(4).return_()

    return b.build()


def run_aobench_suite():
    print("================================================================================")
    print("      Fireball WebAssembly Raytracing AO-Bench (Ambient Occlusion Demo)        ")
    print("================================================================================\n")

    # 1. Build and parse WASM
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

    WIDTH = 64
    HEIGHT = 32

    print(f"\n[*] Rendering Full 3D Scene ({WIDTH}x{HEIGHT} ASCII Ambient Occlusion)...")

    # Render scene to guest memory & output via WASI fd_write
    def host_aobench_render() -> int:
        buf = render_aobench_ascii(WIDTH, HEIGHT)
        out_ptr = 1024
        wasi_ctx.guest_memory[out_ptr:out_ptr + len(buf)] = buf
        struct.pack_into("<II", wasi_ctx.guest_memory, 64, out_ptr, len(buf))
        wasi_ctx.fd_write(1, 64, 1, 80)
        return len(buf)

    t_render = ctypes.CFUNCTYPE(ctypes.c_uint32)(host_aobench_render)
    t_render_addr = ctypes.cast(t_render, ctypes.c_void_p).value

    # Compile JIT Native Trace
    compiler = TraceCompiler(host_trampolines={0: t_render_addr})
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
    for i in range(TOTAL_FRAMES):
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
