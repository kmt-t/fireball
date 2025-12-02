# `src/` Folder Structure

The `src/` directory contains the implementation files and main logic for the Fireball project. Its structure mirrors the `inc/` directory, providing corresponding implementations for the interfaces defined in the header files.

## Directory Layout:

```
src/
├── main.cxx              // Main program entry point
├── allocator/            // Implementations for memory allocators
├── coos/                 // Implementations for COOS kernel components
├── hal/                  // Implementations for HAL (Hardware Abstraction Layer) drivers
├── vsoc/                 // Implementations for Virtual System-on-Chip (vSoC) components
└── utils/                // Implementations for general utilities
    └── backtrace.cxx     // Stack backtrace implementation
```

## Guidelines:

-   **Implementation Focus**: This directory is solely for the implementation (`.cxx`, `.c`) files corresponding to the interfaces defined in `inc/`.
-   **Mirror `inc/`**: The subdirectory structure here should generally mirror that of `inc/` to maintain consistency and ease navigation.
-   **No Placeholders for Non-Existent Files**: Only existing implementation files should be moved into these directories. New files should be added as they are developed.
-   **Modularity**: Each component's implementation should be self-contained within its respective directory.
