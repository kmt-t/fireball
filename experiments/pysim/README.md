# pysim -- one full-set experimental Python system

A single, runnable Python implementation of a slice of Fireball end to end:
scheduler, dictionary logger, guest console output, HAL buffers, the
recovery-strategy contract, and the real `fireball_call` syscall ID table
(`system_syscall.md` §5) dispatched over a real vMMIO controller and a real
IPC router, PLUS a from-scratch WASM binary parser, a real **threaded (CPS)**
reference interpreter, and a real Copy-and-Patch **x64** JIT compiler that
executes actual `.wasm` bytes as real machine code -- including WASM `call`
reaching a guest all the way out through `fireball_call` into
`WASI_FD_WRITE`'s real console output and `IPC_SEND`/`IPC_RECV`'s real,
URI-routed, ownership-transferring message queue. Everything lives in this
one folder and is wired into one `main.py` run -- no sub-folders, no
external WASM tooling or runtime library (no wasmtime, wasm3, or anything
else) anywhere in the import graph. C++ naming/type conventions were set
aside on purpose so the Python could stay natural; this is fully self-contained
within `experiments/pysim/` and does not import any `docs/**/concepts` files.

`system.py` uses self-contained simulation modules (`vmmio.py`, `ipc_router.py`,
`platform_memory.py`, `system_containers.py`) that implement the exact
vMMIO permission/dispatch mechanism, IPC routing/ownership-handoff, and
physical memory/MPU management in Python.

## Why it exists

1. Pressure-test whether the *design* in `docs/components/tier1_interface/interface_wit.md`,
   `docs/components/tier1_core/system_logging.md`, `docs/components/tier1_core/os_scheduler.md`,
   `docs/components/tier1_core/system_syscall.md` and
   `docs/components/tier3_platform/platform_hal.md` holds together once
   something has to really execute, not just parse as consistent prose.
2. Get a real `.wasm` binary running end to end, JIT-compiled to x64,
   entirely hand-written -- the interesting part of Fireball's own design
   (Copy-and-Patch: compile-time stencil templates, runtime copy+patch) --
   with a guest able to reach real host services through `fireball_call`,
   not just compute in isolation.

## What's implemented against `docs/specs/wasm_instruction_set.md`

Everything in that spec's i32-only, non-SIMD/threads/GC/EH/tail-call scope
(those are the spec's own declared non-goals, §2, kept as non-goals here
too): all control flow (`block`/`loop`/`if`/`else`/`br`/`br_if`/`br_table`/
`call`/`call_indirect`, `unreachable`, `return`), all i32
arithmetic/bitwise/comparison ops including `clz`/`ctz`/`popcnt`/`rotl`/
`rotr`, `local.get`/`set`/`tee`, `global.get`/`set`, all i32 load/store
widths (`i32.load`, `load8_s/u`, `load16_s/u`, `store`, `store8`,
`store16`) with the bounds-check-then-trap §3.4 mandates before every
access, `memory.size`, `drop`/`select`, a Table+Element section (a single
table, per WASM MVP's own limit) backing `call_indirect`'s runtime
bounds/type check, and function imports bridging to a real host callable
(`system_syscall.md §3`'s `fireball_call`).

**Explicitly not implemented, and why:** i64/f32/f64 (a second, wider
codegen path -- `i64` in particular needs register-pair or 64-bit-native
handling this JIT's "one 8-byte stack slot per value, always via eax"
design doesn't have -- see "Missing spec coverage" below); `memory.grow`
in the JIT specifically (the JIT bakes `mem_size_bytes` into every bounds
check at compile time; the interpreter still supports it as the
correctness oracle for code that doesn't reach it). None of these are
silently missing -- the JIT raises `UnsupportedOpcode`/a clear assertion
rather than miscompiling.

## Missing spec coverage (tracked, not silently dropped)

Per the standing rule that scope isn't this build's call to make, this is
the running list of what a full reading of `docs/specs/wasm_instruction_set.md`
and `system_syscall.md` still calls for, not yet built here:

- **i64/f32/f64 everywhere** (`docs/specs/wasm_instruction_set.md` §3.4/§3.5:
  `i64.load/store`, `f32.load/store`, `f64.load/store`, `i64.const`, and
  every i64/f32/f64 arithmetic/comparison op). This JIT's value-stack
  convention is "one 8-byte native push per WASM value, always read back
  through 32-bit `eax`" -- i64 needs either true 64-bit register-width
  arithmetic (mostly a drop-in `REX.W` variant of the existing i32
  stencils) or, for f32/f64, XMM registers and an entirely separate
  stencil table this experiment has none of yet.
- **`memory.grow` in the JIT** (§3.4, opcode `0x40`): only the interpreter
  implements it; the JIT bakes a fixed `mem_size_bytes` into every bounds
  check at compile time and has no runtime-resizable-memory story.
- **The WASM binary format's Data and Start sections** (section ids 11 and
  8 -- not covered by `wasm_instruction_set.md`, which documents opcodes
  only, but part of the real `.wasm` MVP format this experiment's own
  `wasm_reader.py` targets). Both are currently silently skipped by the
  section-parsing loop (`wasm_reader.py`'s `# else: unsupported section`
  branch) rather than rejected -- a module compiled by a real toolchain
  with static data (almost any nontrivial C/Rust output) would parse
  without error but silently lose its initial linear-memory contents, and
  a module with a start function would silently never run it. This
  experiment's own hand-built test fixtures never emit either section, so
  nothing here has been exercised against them.
- **`br_table` as a real O(1) jump table**: `_emit_br_table` in
  `x64_jit.py` is a linear `cmp`/`je` compare-chain, not the hardware
  `TBB`/`TBH`-style table branch the spec's "物理動作" column describes for
  the real Cortex-M33 target. Functionally equivalent for every case
  tested here (a handful of arms); would need revisiting before trusting
  it against a `br_table` with many arms.
- **vMMIO Generic's MMIO_BULK_READ/WRITE and VDMA_START only reach the
  FC=15 PASSTHROUGH test window and guest RAM, not FC=14 SHM.** `hal.py`'s
  `ShmBufferPool` (the HAL/IPC bus's own shared-memory implementation) and
  `system.py`'s vMMIO SHM registration are two independent things -- a
  real system has exactly one "shared memory." `VMMIOController.map_shm_page`
  is imported and exercisable (see `vmmio_concept.py`'s own tests), but
  nothing in this build yet registers a `ShmBufferPool` handle as an FC=14
  vMMIO page, so a guest reaching SHM via a raw vMMIO address and a guest
  reaching it via `BusMaster`/`BusSlave`'s `shm-slice` handles would land on
  two different bytearrays today.
- **No WASM module validation / operand-stack type-checking at load time.**
  A real WASM engine rejects a module whose bytecode doesn't balance its
  declared stack effects before ever running it. This experiment's parser
  and JIT trust that `wasm_builder.py`-encoded fixtures are well-formed and
  simply execute whatever stack pushes/pops the bytecode contains -- a
  `call` site short one argument silently underflows into whatever the
  caller's frame had below it instead of being rejected at load time (see
  bug 10 below, found by exactly this).
- **Whether f32/f64 *arithmetic* (not just load/store) is actually in this
  hypervisor's scope is genuinely unclear from `wasm_instruction_set.md`
  alone**: §3.4 lists `f32.load`/`f64.load`/`f32.store`/`f64.store`, but §3.5
  ("整数算術・論理・比較命令") stops at `i32.rotr` with no f32/f64 arithmetic
  rows at all -- unlike i64, which at least gets `i64.const`/`i64.load`/
  `i64.store` entries implying real i64 values flow through the system.
  This could mean "floats only ever pass through as opaque load/store
  payloads, no WASM-level float arithmetic is supported" (a deliberate,
  spec-consistent MVP boundary for a 32KB-RAM target with no mandatory
  FPU) or it could mean the table is simply incomplete. Flagging this
  rather than guessing which.

**Outside this experiment's scope entirely** (real, large subsystems of
their own that a `docs全部` reading would also include, not attempted
here): the GDB RSP debug server, vMMIO paging/TLB, HAL device drivers
(GPIO/timer/bus as physical peripherals), the full COOS coroutine engine
with CSP channels, and MPU-based memory isolation. `hal.py`/`system.py`
model the *contracts* those components expose (SHM handles, a syscall
bridge, a scheduler shape) at the granularity needed to test the contracts
in `interface_wit.md`, not their internal hardware-facing implementations.

## Files

| File | What it is |
| :--- | :--- |
| `hal.py` | `UartTransport` (real `socket.socketpair()`), `ShmBufferPool` (handle+bounds+ownership-checked buffers), `Timer` |
| `recovery.py` | `{META_RecoveryStrategy}`: ignore/retry/restart/panic, retry/backoff, the retry-exhaustion escalation |
| `logger.py` | `Logger` (build-time dictionary logging) and `ConsoleOutput` (raw-byte `wasi:cli/stdout`) sharing one transport |
| `system_containers.py` | Zero-allocation static system container vocabulary (`FlatMapView`, `FlatSetView`, `RadixBinaryTreeView`, `BitView`, `StaticFlatMap`, `StaticFlatSet`, `RingBuffer`, `StaticVector`) |
| `vmmio.py` | Self-contained `VMMIOController` (3-tier security gate, 20-bit VPN FlatMap, 16-slot Folding XOR TLB) |
| `ipc_router.py` | Self-contained `IPCRouter` (3-stage routing, RBAC matrix, zero-copy Revoke/Enqueue/Grant handoff) |
| `platform_memory.py` | Self-contained `MemoryManager` & `PMSAv8MPU` (fixed-size partition leasing, typed slot pools, RAII `SharedBlock`) |
| `system.py` | Wires HAL/logger/recovery together; `BusMaster`/`BusSlave` implement `shm-slice`; the real `fireball_call` ID table (`FbSyscallId`) over `VMMIOController` + `IPCRouter` + `MemoryManager` + real `WasiErrno` codes |
| `main.py` | Runs the HAL/logger/scheduler/recovery demo, then the WASM/JIT demo (a guest reaching `WASI_FD_WRITE` and a real `IPC_LOOKUP`/`SEND`/`RECV` round trip via `fireball_call`), and reports findings |
| `tests.py` | Full invariant test suite (93 tests covering COOS, scheduler, memory, HAL, logger, recovery, JIT, vMMIO, IPC, syscalls, WASI, and containers) |
| `leb128.py` | LEB128 varint encode/decode |
| `wasm_module.py` | In-memory parsed-module representation (`FuncType`, `Function`, `Import`, `Export`, `Memory`, `Global`, `Table`, `Element`) |
| `wasm_opcodes.py` | The one opcode table both the interpreter and the JIT compile against |
| `wasm_reader.py` | The real binary `.wasm` parser (Type/Import/Function/Table/Memory/Global/Export/Element/Code sections) |
| `wasm_builder.py` | A minimal *encoder*, used only to synthesize test fixtures (see below) |
| `control_flow.py` | Shared instruction decoding + block/loop/if nesting resolution, used by both the interpreter and the JIT |
| `interpreter.py` | Reference WASM interpreter as a real **threaded (CPS) interpreter** -- a per-opcode handler table, each handler taking the spec's exact 4-argument signature (`ip, stack_bot, env, local_base`) and returning its own next continuation, not a shared if/elif loop deciding on a handler's behalf -- the correctness oracle the JIT is checked against |
| `x64_asm.py` | Generic register-name-driven x64 encoders (push/pop/mov/call-reg/...) for calling-convention glue that a fixed stencil table can't parametrize |
| `x64_stencils.py` | Copy-and-Patch x64 stencils, each built by a **generator drained once** to simulate `constexpr`; multi-relocation stencils use sentinel-byte auto-discovery instead of hand-counted offsets |
| `x64_jit.py` | The real JIT: control-flow compilation, branch/call/bounds-check-trap relocation, cross-function layout, the `fireball_call` host-call bridge |
| `exec_memory.py` | Cross-platform real executable memory (Windows `VirtualAlloc` & Linux `mmap`/`mprotect` with strict W^X) + `ctypes` function-pointer creation |
| `test_x64_asm.py` | Every generic encoder, executed as real machine code |
| `test_x64_stencils.py` | Every stencil, executed as real machine code and checked against Python-computed expected values |
| `test_x64_jit.py` | End-to-end: build real `.wasm` bytes -> parse -> JIT -> execute -> cross-check vs. the interpreter |
| `test_host_call.py` | The `fireball_call` bridge in isolation: every arity 0-7, register+stack marshalling, ABI alignment |
| `test_concept_differential.py` | Differential equivalence test suite asserting 100% behavioral identity between `experiments/pysim` and `docs/**/concepts` |
| `aobench.py` | Full 3D Raytracing Ambient Occlusion Benchmark (AO-Bench) running via WASM, JIT trace execution, and WASI `fd_write` |

## No existing WASM tooling

There is no `wat2wasm` and wasmtime is explicitly off-limits in this
sandbox, so `wasm_builder.py` is a small from-scratch *encoder* used only
to synthesize test fixtures (see below) -- it is test-fixture tooling, not part
of the design under test. `wasm_reader.py` (the real parser) only ever
sees genuine binary-format bytes produced by it, and `main.py`'s demo
writes a module built this way to an actual file on disk and reads the raw
bytes back before parsing, so the whole pipeline really does go through a
`.wasm` file, not an in-memory shortcut.

## constexpr, simulated with a generator

The real Fireball JIT builds each Copy-and-Patch stencil once via a C++20
`constexpr` function, baking a fixed byte array into ROM; the JIT then only
ever copies that array and patches a few relocation slots. Python has no
constexpr, so `x64_stencils.py` builds each stencil with a **generator**
that is drained exactly once, at import time, into an immutable `bytes`
object (`_materialize()`/`_materialize_auto()`) -- the single drain stands
in for "compile-time evaluation," and every actual JIT compilation
afterward only ever touches the frozen result. Stencils with more than one
relocation (memory bounds checks, globals) fill their placeholder bytes
with distinct sentinel patterns and let `_materialize_auto()` discover the
real offsets by searching for them, rather than a second hand-count --
hand-counting the *first* four bugs below is exactly what this replaced.

## Running it

```bash
# from this directory, with any Python 3.11+ (stdlib + ctypes only)
python tests.py
python test_concept_differential.py
python test_x64_asm.py
python test_x64_stencils.py
python test_x64_jit.py
python test_host_call.py
python aobench.py
python main.py
```

## What building this actually found

Bugs, all in this sandbox's own code (fixed, and covered by a regression
test so they can't silently come back):

1. **`LogDictionary.format()` crashed on any format string using fewer
   than 4 specifiers.** Python's `%` operator raises `TypeError` if handed
   more positional arguments than the format string consumes, where C's
   variadic `printf` just ignores the extras. Fixed by counting the actual
   specifiers and slicing the argument tuple to match.
2. **Four x64 encoding bugs** in the original stencil set, found by
   hand-auditing every byte against the Intel encoding rules and then
   confirmed by actually executing each stencil: `local.set`/`local.tee`'s
   relocatable displacement pointed at the wrong offset, and
   `i32.load`/`i32.store`'s REX prefix wrongly set REX.X instead of REX.B.
3. **Returning from a function whose body ends in `i32.store`** (which
   leaves nothing on the stack) popped the native call's own return
   address into the "return value" instead, corrupting the stack on `ret`.
   `x64_jit.py` now always picks the epilogue from the function's declared
   result arity, never from what a test happened to leave on the stack.
4. **Nested WASM `call` clobbering `r10`/`r11`** (the JIT's own
   locals-pointer/memory-base registers) because the callee's prologue
   overwrites them with its own -- caught by reasoning through the calling
   convention, not by a crash.
5. **Every callee-saved register (`rbx`, `r12`-`r15`) was left
   unprotected across a call back into ctypes.** The Microsoft x64 ABI
   makes these callee-saved, but PROLOGUE/EPILOGUE never saved or restored
   any of them, and nearly every stencil uses `rbx` as scratch. This
   silently corrupted whatever CPython itself kept in those registers --
   sometimes nothing observable, sometimes a hard segfault depending on
   what else was live at the call site. Found by `test_x64_asm.py`
   segfaulting the *interpreter itself* on the very first register tested
   (`r13`), not by a wrong answer. Now every compiled function's prologue
   saves all five and every epilogue restores them.
6. **The `fireball_call` host-bridge's stack-alignment arithmetic was
   inverted**: it rounded the pre-`call` stack reservation to "≡8 mod 16"
   when the ABI actually requires "≡0 mod 16" immediately before `call`
   executes (`call`'s own return-address push is what brings the callee to
   the "16-aligned minus 8" every function sees at entry). This
   segfaulted *any* call into a real ctypes callback, even with zero
   arguments, and was isolated by shrinking the failing case down to two
   instructions (`sub rsp, N` + `call`) outside the full JIT until the
   exact byte count that broke it was obvious.
7. **The same bridge popped WASM call arguments *after* already pushing
   `r10`/`r11` to save them**, so the "argument" pops actually retrieved
   the just-saved register values instead of the real arguments -- no
   crash, just silently wrong data, caught by `test_host_call.py` checking
   recorded argument values against distinct, position-identifiable
   numbers rather than a checksum that could pass by cancellation.
8. **A second trap at a *different* native stack depth than a previous
   trap in the same process reliably crashed the whole Python process**,
   even though a single trap by itself always raised a clean, catchable
   `OSError`. Root cause: this JIT's executable buffers have no registered
   Windows unwind metadata (`.pdata`/`.xdata`), so when an access violation
   occurs partway through a function body (after some WASM-value-stack
   pushes beyond the fixed prologue), the OS exception unwinder falls back
   to treating the faulting frame as a leaf function -- which only
   recovers correctly by coincidence when the *current* depth happens to
   match whatever depth a *previous* successful recovery already primed
   the unwinder's assumptions for. This was invisible for the whole prior
   test suite because it only ever exercised one trap (the memory-bounds
   one) per process; it surfaced the moment `call_indirect`'s three new
   trap sites (bounds/type/uninitialized-slot) ran a second, differently-
   shaped trap in the same run, corrupting the process outright instead of
   raising `OSError`. Isolated with a minimal standalone repro (a bare
   prologue + N padding pushes + jump-to-TRAP, varying N and call count)
   that reproduced the flip from catchable to fatal with a single extra
   8-byte push. Fixed by giving every stencil-compiled function a
   permanent, dedicated "restore point" register (`rdi`, otherwise unused
   anywhere in this codebase, now saved/restored like a genuine sixth
   ABI callee-saved register): `PROLOGUE` captures `rsp` into `rdi`
   immediately after its five existing pushes, and `TRAP` now snaps `rsp`
   back to `rdi` and unwinds those same five registers *before* the
   deliberate null-pointer dereference -- so every trap, anywhere in a
   function's body, faults at the exact same fixed depth relative to that
   function's own entry, regardless of how deep the WASM operand stack had
   grown. This only fixes single-level traps (a trap in the outermost
   function ctypes calls, or one that unwinds cleanly back through a
   *normal* return chain); a trap several *nested* WASM `call`/
   `call_indirect` levels deep, where an intermediate caller frame itself
   still has body-level pushes live at the point of the inner call, is not
   proven safe and isn't exercised by any current test -- true general
   correctness would need real per-function `RtlAddFunctionTable` unwind
   info, which this experiment does not attempt.

9. **`system.py` invented `FB_SYSCALL_LOG=1`/`FB_SYSCALL_IPC_SEND=2` instead
   of reading `system_syscall.md` §5's real ID table.** `0x01` there is
   already `SYS_YIELD`, not a log call, and real `IPC_SEND` is `0x40`, not
   `2` -- so the wire IDs this build shipped simply didn't correspond to
   anything a real Fireball guest could call. Worse, there was never a
   legitimate "guest calls the dictionary logger" syscall to model in the
   first place: `system_logging.md` §1 explicitly scopes that logger to
   "build-time-registered internal state logs only" and names
   `wasi:cli/stdout`/`stderr` (`interface_wit.md` §5.5's `console-output`)
   as the *only* guest-facing text-output path. Fixed by adopting the real
   `FbSyscallId` table wholesale (System/vMMIO Generic/VDMA/IRQ/IPC/WASI,
   `system.py`'s `FbSyscallId`), routing guest output through
   `WASI_FD_WRITE`, and reusing the real `VMMIOController`/`IPCRouter`
   concept implementations instead of a made-up dispatch table. Also
   switched the raw `fireball_call` return value from two project-invented
   sentinel constants to the real WASI `errno_t` numbering
   (`system.py`'s `WasiErrno`) the spec's §4.2 actually calls for.
10. **`main.py`'s new IPC_LOOKUP call site pushed only 6 of the 7 required
    `i32` arguments** (missing the last zero-padding arg), silently
    underflowing the WASM operand stack by one slot instead of being
    rejected -- and it *did* run, corrupting `local.set(0)`'s target and
    crashing the whole process with an unrecoverable access violation
    (not even a catchable `OSError`, since the corruption happened well
    before reaching any bounds-checked trap site). Bisected by shrinking
    the guest function step by step (WASI_FD_WRITE alone: fine; a trivial
    7-arg host import + `local.set`: fine; the *real* `IPC_LOOKUP` call as
    written: crashed) until the argument count was the only difference
    left. This is also what surfaced the "no WASM validation" gap above --
    a real WASM loader's stack-effect validation would have rejected this
    module before it ever ran.

Two spec gaps (`recovery.py`'s docstrings) this build had to resolve by
assumption rather than by fixing code: what happens after the 3rd failed
retry, and whether IPC queue-full is `ignore` or `retry`. Neither is a code
bug; both are places `interface_wit.md` should say more than it currently
does.
