# Fireball Hypervisor

Fireball is a lightweight WebAssembly (WASM) hypervisor designed for embedded systems. It targets Cortex-M33, RISC-V/32, and Linux platforms, aiming to provide virtualization with a minimal footprint using only standard C/C++ runtimes.

## Concepts and Features

The design of Fireball is built on three core pillars: a "Cooperative Scheduler" that operates safely on a single thread, "Ownership-based IPC" that clarifies data responsibility, and "Flexible Static Configuration."

- **Safe Multitasking**: Data races are prevented through scheduling by COOS (Cooperative OS) and CSP-based communication. Interrupts and event coordination are handled smoothly as tasks voluntarily yield control.
- **Ownership-Aware Communication**: By combining explicit ownership transfer with shared memory management by the IPC Router, memory can be safely passed between subsystems.
- **Predictable Behavior**: Heap and buffer sizes are fixed upfront through header-based static configurations. This allows for precise control over system behavior, even in memory-constrained environments.

## Key Components

- **COOS Kernel**: Manages task switching, communication, interrupt handling, and memory isolation to achieve efficient multitasking.
- **IPC Router**: Handles URI-based service discovery, access control, and message forwarding. Its efficient communication protocol balances low latency with memory efficiency.
- **vSoC Runtime**: Includes the WASM execution runtime, debugger, and JIT compiler. It also mediates hardware access through communication with the host.
- **HAL and Services**: Provides hardware operations for UART, GPIO, and other peripherals via IPC. WASI and Libc wrappers are also available as services.

## Development Environment and Build

Fireball uses the CMake build system. C99 and C++20 code is compiled with clang, supporting builds for various environments including Cortex-M33, RISC-V/32, and x86.

## Setup

You need Clang, CMake, and Ninja to build the project.

### 1. Prerequisites
- Clang (13+)
- CMake (4.2+)
- Ninja
- Python 3.14+ (pyenv recommended)
- wit-bindgen
- TLA+ (TLC)

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
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

All Fireball development is based on the specifications stored in `docs/`. Before implementing changes, please check the relevant documentation and adhere to the development policies defined in `.claude/rules/`.

- **Design Documents**: `docs/components/`
- **Development Guidelines**: `.claude/rules/development-policy.md`
- **Resource Budget**: `docs/architecture/resource_budget.md`

## License

Simplified BSD License - See the [LICENSE](LICENSE) file for details.
