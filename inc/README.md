# `inc/` Folder Structure

The `inc/` directory contains public header files and shared interfaces for the Fireball project. It is structured to clearly define the responsibilities of each component and enhance code readability, maintainability, and testability.

## Directory Layout:

```
inc/
├── fireball_config.hxx   // Global configuration macros (e.g., max coroutines, heap sizes)
├── commons.hxx           // Common utilities, definitions, and macros shared across the project
├── fireball.hxx          // Main Fireball API/facade header, providing a high-level interface
├── allocator/            // Interfaces for various memory allocators
├── coos/                 // Interfaces for COOS (Cooperative Operating System) kernel components
├── hal/                  // Hardware Abstraction Layer (HAL) interfaces
└── utils/                // General utility headers
    └── backtrace.hxx     // Stack backtrace utilities
```

## Guidelines:

-   **Interface Definition**: Only interface definitions (classes, structs, function declarations) should reside here. Implementations should go into the corresponding `src/` directory.
-   **Dependencies**: Headers should minimize dependencies on other headers where possible to reduce compilation times and avoid circular dependencies.
-   **Self-contained**: Each header file should be self-contained and include all necessary dependencies, using include guards.
