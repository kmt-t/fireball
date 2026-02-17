# Specification Gap Backlog

This document tracks missing details in the natural language specifications that are required for a complete WIT definition and TLA+ verification.

## 1. Loader (module_loader)
- [ ] **Dependency Management**: How are cross-module imports resolved? (WIT currently assumes single module).
- [ ] **Module Lifecycle**: Specifics of `unload` and resource cleanup.
- [ ] **Memory Constraints**: Maximum size of `module_view` and index tables.
- [ ] **Verification Levels**: What exactly is checked in "Lightweight Verifier"? (Header, checksum, or basic instruction sanity?)

## 2. Logger (logging)
- [ ] **WIT Interface**: Formally define the `logger` interface in `services.wit` (missing from current WIT).
- [ ] **Dictionary Management**: Define the structure and location of the log dictionary (ROM-based?).
- [ ] **Buffer Policy**: Formalize behavior when the ring buffer is full (Overwrite vs. Drop).
- [ ] **Flush Interface**: Define how the COOS Idle Hook interacts with the Logger.

## 3. vSoC and vMMIO
- [ ] **VDMA Detail**: Define the WIT representation of VDMA operations (are they system calls or pure MMIO traps?).
- [ ] **Virtual Interrupts**: Explicit mapping between physical interrupts and virtual IRQ IDs.
- [ ] **Trap Handling**: Detail the exact state saving/restoring protocol for `fireball-call`.

## 4. Memory Management
- [ ] **Ownership Metadata**: How is `task-id` associated with a memory block in the implementation?
- [ ] **Shared Memory Lifecycle**: Define who is responsible for freeing `shared` partition blocks.

## 5. IPC and Channels
- [ ] **Handoff Conditions**: Precisely define when a `send` / `recv` triggers a scheduler context switch (TLA+ verified logic needs to be reflected in WIT contracts).
- [ ] **Message Format Stability**: Finalize the bitfield layout of `kv-pair` and ensure it accommodates all planned service types.
