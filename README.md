# Fireball Hypervisor

Fireball is a lightweight WebAssembly (WASM) hypervisor designed for embedded systems. It targets Cortex-M33, RISC-V/32, and Linux platforms, aiming to provide virtualization with a minimal footprint using only standard C/C++ runtimes.

## Concepts and Features

The design of Fireball is built on three core pillars: a "Cooperative Scheduler" that operates safely on a single thread, "Ownership-based IPC" with Hoare CSP rendezvous communication, and "Flexible Static Configuration."

- **Safe Multitasking**: Data races are prevented through FIFO Round-Robin scheduling by COOS (Cooperative OS) and Hoare CSP rendezvous message passing. Interrupts are posted asynchronously to ring buffers and processed cleanly at explicit task yield points.
- **Ownership-Aware Communication**: By combining explicit zero-copy ownership transfer (Revoke -> Enqueue -> Grant) with shared memory management by the IPC Router, memory and channels can be safely passed between subsystems without data races.
- **Predictable Behavior & Safety**: Heap and buffer sizes are fixed upfront through header-based static configurations (`constexpr`). JIT code cache enforces MPU W^X separation (RW+XN during compilation, RO+X during execution with `__DSB()/__ISB()` barriers).

## Key Components

- **COOS Kernel (Tier 1)**: Single-threaded cooperative OS running a pure FIFO Round-Robin scheduler with an isolated Idle slot, managing coroutines (`co_yield`), interrupt flags, and memory isolation.
- **IPC Router (Tier 1)**: Handles URI-based service discovery, role-based access control, and zero-copy channel ownership handoffs following strict acyclic client-server topologies.
- **vSoC Runtime & JIT Compiler (Tier 2)**: Includes the WASM execution engine, debugger, and 3-bank rotation JIT compiler (Active / Warm / Oldest) with MPU-enforced W^X protection.
- **Platform & HAL (Tier 3)**: Provides hardware abstraction and drivers (UART, GPIO, Timers) via IPC and WIT (WebAssembly Interface Types) specifications.

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

Run the integrated 8-gate verification pipeline (Format, Traceability, Hierarchy, Formal model checking with mutation tests, WIT interfaces, Evidence backing, Verification obligations, and Consistency lockfiles):

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1 -clean

# Linux / macOS / WSL
./tools/run_all_tests.sh --clean
```

To run risk assessment and quality gates together:
```powershell
powershell -ExecutionPolicy Bypass -File tools/run_all_tests.ps1 -assess -noStrict -clean
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

All Fireball development is strictly governed by the specifications in `docs/` and verified by `spec-integrator`. Before implementing changes, adhere to the development policies defined in `.agents/rules/`.

- **Top-Level Requirements**: `docs/requires/`
- **Architecture & Metamodels**: `docs/architecture/`
- **Component Specifications**: `docs/components/` (Tier 1 Core/Interface, Tier 2 Runtime/JIT, Tier 3 Platform)
- **Development Guidelines**: `.agents/rules/development-policy.md`
- **Verification Reports**: `reports/doc_report.md`, `reports/doc_risk_report.md`

## License

Simplified BSD License - See the [LICENSE](LICENSE) file for details.
