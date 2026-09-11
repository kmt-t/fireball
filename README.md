# Fireball Hypervisor

Fireball is a lightweight WebAssembly (WASM) hypervisor designed for resource-constrained embedded systems. It targets Cortex-M33, RISC-V/32, and Linux platforms, aiming to provide safe virtualization with a minimal footprint using C23 and C++23. Standard dynamic allocation APIs (`malloc`/`new`), exceptions, RTTI, and dynamic STL containers are prohibited; bounded project-managed allocators are used only where the architecture explicitly requires them.

## Concept

Fireball is designed around one idea: make a small WebAssembly runtime predictable enough for constrained embedded systems without giving up useful virtualization and isolation. The project follows a specification-first workflow: requirements, architecture, component contracts, formal models, reference implementations, and tests are kept as a traceable design chain.

The runtime is layered so that policy stays separate from mechanism. A cooperative kernel provides bounded task switching and bufferless CSP rendezvous. Ownership moves explicitly across IPC boundaries, allowing shared data to be transferred without copying while preventing accidental data races. Runtime components execute WASM through a compact interpreter and selectively use Copy-and-Patch JIT code, with protected code and data regions.

The design favors static resolution and bounded resources. Configuration fixes capacities at build time, memory is divided by purpose, and access to guest memory and virtual devices is checked at the boundary. The implementation policy excludes exceptions, RTTI, and unbounded standard containers; the detailed memory and allocator contracts are defined in `docs/`.

The architecture is organized into three tiers:

- **Tier 1**: COOS, IPC, configuration, static containers, memory contracts, services, and WIT interfaces.
- **Tier 2**: vSoC runtime, loader, interpreter, system calls, logging, vMMIO, debugger, and HAL dispatch.
- **Tier 3**: JIT compilation/runtime, the `libfireball` guest adapter, and physical platform drivers.

## Development Environment and Build

Fireball uses standard CMake and Ninja build systems. C23 and C++23 code (using C++20 coroutine facilities, C++23 concepts, `constexpr`, and `[[clang::musttail]]`) is compiled with **Clang 17+**, with primary targets for Cortex-M33 (ARMv8-M Mainline with TrustZone and MPU), RISC-V/32, and host development environments (x86_64 / Linux).

> [!IMPORTANT]
> **Clang is strictly required**. Fireball's interpreter dispatch and execution engine rely on `[[clang::musttail]]` for zero-stack overhead direct tail calls. GCC and MSVC are not supported.

## Setup

You need Clang, CMake, Ninja, and Python (with `uv` recommended) to build and verify the project.

### 1. Prerequisites
- **Toolchain & Build**:
  - Clang (17+ mandatory: `clang` / `clang++`)
  - CMake (3.25+)
  - Ninja
- **Python Runtime & Package Management**:
  - Python 3.11+ / 3.14+ ([`uv`](https://github.com/astral-sh/uv) recommended)
- **Python Dependencies**:
  Install all required Python modules using [requirements.txt](requirements.txt):
  ```bash
  # Using uv (fast & recommended):
  uv pip install -r requirements.txt

  # Or using standard pip:
  pip install -r requirements.txt
  ```
  Key modules include:
  - **Verification Engine (`spec-integrator`)**: `pyModelChecking` (CTL/LTL formal verification), `mistune` (Markdown AST parser), `pyyaml`, `requests`, `urllib3`, `mermaidx`.
  - **Simulator & JIT Machine Code (`experiments/pysim`)**: `wasmtime` (WASM reference runtime for differential testing), `unicorn` (CPU emulator for Thumb-2 instruction trace verification).
  - **Testing & Code Formatting**: `pytest`, `pytest-cov`, `ruff` (fast linter and formatter).

### 2. Verification & Quality Gates
Fireball enforces an automated verification pipeline (`spec-integrator`) ensuring static formatting, keyword traceability, tier hierarchy encapsulation, pyModelChecking formal verification, WIT interface types, evidence backing, verification obligations, and consistency baselines (see `tools/README.md`):

```powershell
# Document Quality Gates (8 Gates, Cost: 0)
powershell -ExecutionPolicy Bypass -File tools/check-doc.ps1   # Windows
./tools/check-doc.sh                                           # Linux / macOS / WSL

# Source Code Quality & Anti-Sabotage Check (Cost: 0)
powershell -ExecutionPolicy Bypass -File tools/check-src.ps1 -group all   # Windows
./tools/check-src.sh -g all                                               # Linux / macOS / WSL
```

For Cloud LLM Semantic Audit (milestone / release gate — explicit user instruction only):
```powershell
powershell -ExecutionPolicy Bypass -File tools/risk.ps1               # risk assessment
powershell -ExecutionPolicy Bypass -File tools/llm-keyword-review.ps1 # high-risk island audit
```

## How to Build

### 1. Host Environment (x86_64 / Linux / Windows)
```bash
mkdir cmake-build
cd cmake-build
cmake -G Ninja -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ ..
ninja
```

### 2. Embedded Target (ARM Cortex-M33 Cross-Compilation with Clang)
```bash
mkdir cmake-build-m33
cd cmake-build-m33
cmake -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/arm-clang.cmake -DTARGET_CPU=cortex-m33 ..
ninja
```

### 3. Python Reference Simulator (`experiments/pysim/`)
```bash
# Run all 11 end-to-end integration scenarios
uv run --system-certs --with wasmtime python experiments/pysim/scenarios/run_all.py

# Run 3D AO-Bench (Ambient Occlusion) benchmark
uv run --system-certs --with wasmtime python experiments/pysim/aobench.py
```

## Documentation and Development Process

All Fireball development is strictly governed by the specifications in `docs/` and verified by `spec-integrator`. Document links utilize unified `{Keyword}` anchor tokens recorded in the keyword dictionary to eliminate fragile file-name and section-number references.

- **Top-Level Requirements**: `docs/requires/requirement_list.md`
- **Keyword Dictionary (Link Registry)**: `docs/architecture/keyword_dictionary.md`
- **Architecture, Document Structure, and Resource Budget**: `docs/architecture/architecture_overview.md`, `docs/architecture/document_structure.md`, `docs/architecture/resource_budget_estimation.md`
- **Component Specifications**: `docs/components/` (Tier 1 Core/Interface, Tier 2 Runtime, Tier 3 JIT/Platform)
- **Physical Specifications**: `docs/specs/` (WASM, WASI, GDB RSP, and JIT stencil catalogs)
- **Integration Test Scenarios**: `docs/architecture/integration_test_scenarios.md`
- **Roadmap & Backlog**: `docs/plans/roadmap_phase.md`, `docs/plans/backlog_list.md`
- **Tooling and Validation**: `tools/README.md`, `.agents/skills/document-validation/`

## License

Simplified BSD License - See the [LICENSE](LICENSE) file for details.
