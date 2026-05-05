# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Vision & Architecture
Fireball is a WASM hypervisor for embedded systems (RAM < 64KB, target Cortex-M/RISC-V).
- **Core Philosophy**: Zero-cost abstraction, static memory management, and deterministic, stackless execution.
- **Architectural Pattern**: vSoC utilizing the **Harness Pattern** for dependency injection.
- **Interfaces**: **WIT (WebAssembly Interface Types)** is the single source of truth for all interfaces. Implementation code (C++) is derived from WIT.

## Development Constraints & Standards
Adhere strictly to the rules in `.claude/rules/subrules/`:
1. **embedded_cpp.md**: Strict memory management (no heap/dynamic containers), RAII, and type-safety (no `void*`).
2. **design_philosophy.md**: Contract-first design, clean architecture, and strict traceability.
3. **documentation.md**: Documentation is authoritative.

## Tracing & Reference Rules
- **Traceability**: All architectural decisions must trace to requirements (marked with `{Keyword}`) in `docs/requires/`. References to these keywords must be maintained in all design documents (`docs/components/`).
- **Documentation Policy**: 
    - Before implementation/refactoring, read the component spec in `docs/components/`. 
    - Architectural changes must be reflected in the design specs.
    - Component design must follow the format defined in `docs/components/FORMAT.md`.
    - Consistency with `docs/components/CHECKLIST.md` is required for self-review.

## Common Tasks & Validation
- **Build**: Use `ninja` within `cmake-build-*/` directories.
- **Validation**: Before committing, run the cross-sectional validation script to check keyword annotations and tier compliance.
- **Naming**: No prefixes/postfixes. POD members use `snake_case`.
