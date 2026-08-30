# Fireball Hypervisor

Fireball is a lightweight WebAssembly (WASM) hypervisor designed for embedded systems. It targets Cortex-M33, RISC-V/32, and Linux platforms, aiming to provide virtualization with a minimal footprint using only standard C/C++ runtimes.

## Concepts and Features

The design of Fireball is built on three core pillars: a "Cooperative Scheduler" that operates safely on a single thread, "Ownership-based IPC" with Hoare CSP rendezvous communication, and "Flexible Static Configuration."

- **Safe Multitasking**: Data races are prevented through FIFO Round-Robin scheduling by COOS (Cooperative OS) and Hoare CSP rendezvous message passing. Interrupts are posted asynchronously to ring buffers and processed cleanly at explicit task yield points.
- **Ownership-Aware Communication**: By combining explicit zero-copy ownership transfer (Revoke -> Enqueue -> Grant) with shared memory management by the IPC Router, memory and channels can be safely passed between subsystems without data races.
- **Predictable Behavior & Safety**: Heap and buffer sizes are fixed upfront through header-based static configurations (`constexpr`). JIT code cache enforces MPU W^X separation (RW+XN during compilation, RO+X during execution with `__DSB()/__ISB()` barriers).

## Key Components

- **COOS Kernel & IPC Router (Tier 1)**: Single-threaded cooperative OS running a pure FIFO Round-Robin scheduler with an isolated Idle slot, managing coroutines (`co_yield`), interrupt flags, role-based IPC routing, and zero-copy rendezvous channel handoffs (`docs/components/tier1_core/`, `docs/components/tier1_interface/`).
- **vSoC Subsystem & Interpreter (Tier 2)**: WASM execution engine featuring a stackless fast interpreter with a unified stack layout (inlining context, CallFrames, locals, and operands), vMMIO virtual address router with TLB cache, and GDB RSP debug controller (`docs/components/tier2_runtime/`).
- **Copy-and-Patch JIT Subsystem (Tier 3)**: Near-zero compilation cost JIT engine decomposed from vSoC, utilizing precompiled Thumb-2 stencil templates, constexpr assembler, and a 3-bank rotating code cache (Active / Warm / Oldest) with MPU-enforced W^X protection (`docs/components/tier3_jit/`).
- **Platform & Hardware Abstraction Layer (Tier 3)**: Low-level hardware drivers (UART, GPIO, Timers) and physical memory manager exposed via IPC and WIT (WebAssembly Interface Types) specifications (`docs/components/tier3_platform/`).

## Development Environment and Build

Fireball uses the CMake build system. C23 and C++20 code (leveraging C++20 coroutines, concepts, and `constexpr`) is compiled with Clang, with primary targets for Cortex-M33 (ARMv8-M Mainline with TrustZone and MPU), and extensible to RISC-V/32 and x86_64 host environments.

## Setup

You need Clang, CMake, Ninja, and Python with `uv` to build and verify the project.

### 1. Prerequisites
- Clang (17+)
- CMake (3.25+)
- Ninja
- Python 3.11+ / 3.14+ (`uv` recommended)
- `mermaidx` (Embedded QuickJS JavaScript engine for full Mermaid diagram validation)
- `pyModelChecking` (Formal model verification & mutation testing via CTL/LTL)
- `wit-bindgen` (WIT interface validation)

### 2. Verification & Quality Gates

Fireball enforces a strict 8-gate automated verification pipeline (`spec-integrator`) ensuring static format, keyword traceability, tier hierarchy encapsulation, pyModelChecking formal verification, WIT interface types, evidence backing, verification obligations, and consistency baselines:

```powershell
# Windows PowerShell (Fast Pre-Commit Test, Cost: 0)
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1 -sync
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1

# Linux / macOS / WSL
./tools/run_all_tests.sh --sync
./tools/run_all_tests.sh
```

For Milestone & Semantic Audit using Sakura Cloud LLM (Qwen 3.6):
```powershell
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1 -assess -backend sakura
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1 -llm -backend sakura
```

## How to Build

### For Host Environment (x64)
```bash
mkdir cmake-build
cd cmake-build
cmake -G Ninja ..
ninja
```

## Documentation and Development Process

All Fireball development is strictly governed by the specifications in `docs/` and verified by `spec-integrator`. Document links utilize unified `{Keyword}` anchor tokens recorded in the keyword dictionary to eliminate fragile file-name and section-number references.

- **Top-Level Requirements**: `docs/requires/requirement_list.md`
- **Keyword Dictionary (Link Registry)**: `docs/architecture/keyword_dictionary.md`
- **Architecture & Document Structure**: `docs/architecture/architecture_overview.md`, `docs/architecture/document_structure.md`
- **Component Specifications**: `docs/components/` (Tier 1 Core/Interface, Tier 2 Runtime, Tier 3 Platform/JIT)
- **Integration Test Scenarios**: `docs/architecture/integration_test_scenarios.md`
- **Tooling and Validation**: `tools/README.md`, `.agents/skills/document-validation/`

## License

Simplified BSD License - See the [LICENSE](LICENSE) file for details.
