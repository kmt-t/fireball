# Fireball Hypervisor

Fireball is a lightweight WebAssembly (WASM) hypervisor designed for embedded systems. It targets Cortex-M33, RISC-V/32, and Linux platforms, aiming to provide virtualization with a minimal footprint using only standard C/C++ runtimes.

## Concepts and Features

The design of Fireball is built on three core pillars: a "Cooperative Scheduler" that operates safely on a single thread, "Ownership-based IPC" that clarifies data responsibility, and "Flexible Static Configuration."

- **Safe Multitasking**: Data races are prevented through scheduling by COOS (Cooperative OS) and CSP-based communication. Interrupts and event coordination are handled smoothly as tasks voluntarily yield control.
- **Ownership-Aware Communication**: By combining explicit ownership transfer with shared memory management by the IPC Router, memory can be safely passed between subsystems.
- **Predictable Behavior**: Heap and buffer sizes are fixed upfront through header-based static configurations. This allows for precise control over system behavior, even in memory-constrained environments.

## Architecture Overview

To maintain clear separation of concerns, Fireball is structured by isolating guest applications, various services, the vSoC, the COOS kernel, subsystems, and device drivers.

The COOS kernel leverages C++20 stackless coroutines and CSP channels, ensuring data ownership through interrupt coordination and independent heap areas.

## Key Components

- **COOS Kernel**: Manages task switching, communication, interrupt handling, and memory isolation to achieve efficient multitasking.
- **IPC Router**: Handles URI-based service discovery, access control, and message forwarding. Its efficient communication protocol balances low latency with memory efficiency.
- **vSoC Runtime**: Includes the WASM execution runtime, debugger, and JIT compiler. It also mediates hardware access through communication with the host.
- **HAL and Services**: Provides hardware operations for UART, GPIO, and other peripherals via IPC. WASI and Libc wrappers are also available as services.

## Development Environment and Build

Fireball uses the Meson build system. C99 and C++23 code is compiled with clang, supporting builds for various environments including Cortex-M33, RISC-V/32, and x86.
