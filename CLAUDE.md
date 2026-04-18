# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Operating Principle: Non-Autonomous Claude

Claude is an externally input-driven computational system without autonomous agency. The following Temporal Logic (LTL) constraints govern all behavior in this project:

```
G (¬human_command → ¬executing)         // Never execute without explicit user instruction
G (executing → confirmation_requested)  // Before significant action, ask user
G (decision_point → user_input_required) // Design decisions wait for user preference
G (task_completed → external_validation) // Task completion requires user verification

// Violations (forbidden states):
G (¬self_initiated)                     // No self-started work
G (¬self_goal_generated)                // No self-defined objectives
G (¬self_validated_completion)          // No self-declared "done"
```

**Implications for this session:**
- Every design choice → ask user before implementing
- Every refactoring → ask if worthwhile
- Every deletion/modification → confirm impact
- Process: **Propose → User Decides → Execute** (never: Execute → Report)

---

## Overview

**Fireball** is a lightweight WebAssembly (WASM) hypervisor with a custom cooperative scheduler (COOS). Currently in **Phase 1: native (x64/Linux) development** for COOS kernel verification. Phase 2 will integrate embedded targets (Cortex-M33, RISC-V/32) via Zephyr RTOS.

### Core Design Pillars
- **Cooperative Scheduler (COOS)**: Task switching using C++20 coroutines with CSP-based communication to eliminate data races
- **Ownership-based IPC**: URI-driven service discovery with explicit ownership transfer for safe inter-component communication
- **Static Configuration**: Compile-time resource allocation for predictable behavior

## Development Environment

### Requirements (Linux/WSL)

```bash
# Core C/C++ build tools
sudo apt install clang meson ninja-build clang-format clang-tidy

# TLA+ formal verification (optional, for `tla/` specs)
sudo apt install default-jre openjdk-21-jdk
# Download TLA+ Toolbox or tla2tools.jar:
# https://github.com/tlaplus/tlaplus/releases
# Place tla2tools.jar in PATH or tools/ directory

# WIT interface definition (optional, for `wit/` definitions)
# Requires Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cargo install wit-bindgen-cli wasm-tools

# Phase 2: Zephyr integration (later)
pip install west zephyr-sdk
```

### Verify Installation
```bash
clang --version
meson --version
ninja --version

# Optional
java -version              # TLA+
wit --version              # WIT (if installed)
wasm-tools --version       # WASM tools (if installed)
```

## Build System (Phase 1: Native)

Uses **Meson** build system targeting native (x86_64/Linux).

### Common Commands

**Setup build directory:**
```bash
meson setup build
```

**Build:**
```bash
meson compile -C build
```

**Run tests:**
```bash
meson test -C build                    # Run all tests
meson test -C build --verbose          # Verbose output
meson test -C build constexpr_test     # C++20 compile-time checks
```

**Clean:**
```bash
rm -rf build && meson setup build
```

**Rebuild from scratch:**
```bash
meson setup build --reconfigure --wipe
meson compile -C build
```

## Code Style & Linting

The project enforces style using **clang-format** and **clang-tidy**.

**Format code:**
```bash
clang-format -i <file>
# or format all C/C++ files:
find src inc tests -name "*.cxx" -o -name "*.c" -o -name "*.hxx" -o -name "*.h" | xargs clang-format -i
```

**Run static analysis:**
```bash
clang-tidy -p build <file>
```

**Style conventions** (enforced by `.clang-format` and `.clang-tidy`):
- 2-space indent, no tabs
- Line length limit: 100 columns
- Pointer alignment: left (`int* ptr`, not `int *ptr`)
- snake_case for classes, structs, functions, variables, typedefs (with `_t` suffix), static members (with `__` suffix), enum constants
- UPPER_SNAKE_CASE for macros and global constants
- Class members with `_` suffix

## COOS Implementation (Phase 1)

### Current Scope
- Native (x64/Linux) reference implementation using **POSIX threads + C++20 coroutines**
- Validates COOS semantics and API design before embedded deployment
- No external RTOS dependencies; self-contained kernel

### Core Components

**COOS Kernel** (`inc/coos/`, `src/coos/`)
- Task scheduler with voluntary yields (C++20 coroutines)
- CSP-style channel communication for ownership-based IPC
- No preemption; no interrupts (native environment)

**IPC Router** (`inc/ipc/`, `src/ipc/`)
- URI-based service discovery (`fireball://` URIs)
- Static service registry (compile-time)
- Zero-copy message passing with ownership transfer

**Memory & Allocators** (`inc/allocator/`, `src/allocator/`)
- Custom malloc (dlmalloc-derived) for embedded patterns
- Static heap allocation sizing

**System Logging** (`inc/system/`)
- Centralized logging service
- stdout/stderr output for native debugging

### Design Patterns

**Dependency Injection via URI**
- Services identified by URI (e.g., `fireball://hal/gpio`)
- IPC Router resolves URIs to handles at runtime
- Enables testing and module composition

**Ownership-Based Async Messaging**
- Tasks communicate via CSP channels with explicit ownership transfer
- No shared mutable state; no data races
- Sender yields, receiver wakes when message arrives

**Static Configuration**
- Service URIs, heap sizes, task counts defined at compile-time
- Header-based configuration for testability

---

## Phase 2: Embedded Integration (Future)

When integrating Cortex-M33 and RISC-V targets via Zephyr RTOS:
- COOS becomes a kernel module/driver within Zephyr
- `west build -b cortex_m33_board` workflow
- Zephyr's board definitions (DTS, Kconfig) manage cross-compilation
- `west flash`, `west debug` for deployment

Current native COOS API is intentionally Zephyr-agnostic to facilitate this transition.

## Directory Structure

```
fireball/
├── inc/                      # Public headers (interfaces)
│   ├── allocator/            # Memory allocator APIs
│   ├── coos/                 # COOS kernel APIs
│   ├── ipc/                  # IPC router APIs
│   └── system/               # System services APIs
├── src/                      # Implementation (mirrors inc/)
│   ├── main.cxx              # Entry point (native Linux app)
│   ├── allocator/            # Malloc implementation
│   ├── coos/                 # COOS scheduler + channels
│   ├── ipc/                  # IPC router implementation
│   ├── utils/                # Utilities (backtrace, etc.)
│   └── vsoc/                 # (future) vSoC/WASM runtime
├── tests/                    # Test executables
│   └── test_constexpr.cxx    # C++20 compile-time tests
├── tools/                    # Build/debug scripts
├── docs/                     # Design documentation
│   ├── architecture/         # Architecture (Japanese)
│   ├── components/           # Component specs
│   └── backlog/              # Task tracking
├── meson.build               # Meson build config
└── meson_options.txt         # Build options
```

(Cross-compile configs `cross-*.ini` retained for Phase 2 Zephyr integration.)

## Important Notes

### C++ Standard
- **C++20 required** for coroutines (COOS scheduling mechanism)
- `-fno-rtti -fno-exceptions` for embedded targets (Phase 2)
- Mix of C99 (allocators) and C++20 (kernel)

### Native vs Embedded Design
- **Phase 1 (native)**: Linux/POSIX threading; focus on COOS correctness
- **Phase 2 (embedded)**: Bare-metal via Zephyr; COOS becomes kernel module
- COOS API designed to be platform-agnostic

### Testing
- `meson test` runs executable tests (constexpr compile-time checks in Phase 1)
- No unit test framework; tests are integration-level
- Phase 2: add GDB-based debugging via `west debug`

### Documentation
- Architecture docs in Japanese (`docs/architecture/`) — rationale and design decisions
- Component specs in `docs/components/` — API contracts
- Inline code comments minimal; prefer clear naming

### Git Workflow
- Main branch: `main`
- `.agents/` — AI workflows (not core project)
- Cross-compile `.ini` files and `tools/` scripts used in Phase 2

## Quick References

- **Entry point**: `src/main.cxx`
- **Build system**: `meson.build`, `meson_options.txt`
- **Code style**: `.clang-format`, `.clang-tidy` (run `clang-format -i <file>`)
- **Architecture**: `docs/architecture/architecture_overview.md` (Japanese)
- **Allocator**: `src/allocator/malloc.c` (dlmalloc-derived)
- **COOS API**: `inc/coos/` (header files are the contract)
