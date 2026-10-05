cmake_minimum_required(VERSION 3.20)
project(fireball)

if(NOT CMAKE_C_COMPILER_ID STREQUAL "Clang" OR
   NOT CMAKE_CXX_COMPILER_ID STREQUAL "Clang" OR
   CMAKE_C_COMPILER_VERSION VERSION_LESS 17 OR
   CMAKE_CXX_COMPILER_VERSION VERSION_LESS 17)
    message(FATAL_ERROR "Fireball requires Clang 17 or newer for C and C++")
endif()

set(CMAKE_POSITION_INDEPENDENT_CODE OFF)
set(CMAKE_C_STANDARD 23)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/inc)

add_executable(fireball
    src/main.cxx
    src/allocator/malloc.c
    src/utils/backtrace.cxx
)
target_compile_options(fireball PRIVATE
    $<$<COMPILE_LANGUAGE:CXX>:-fno-exceptions;-fno-rtti>)
# The project heap exposes only bounded mspace APIs and never replaces libc malloc.
set_source_files_properties(src/allocator/malloc.c PROPERTIES COMPILE_DEFINITIONS
    "MSPACES=1;ONLY_MSPACES=1;HAVE_MMAP=0;HAVE_MORECORE=0;USE_LOCKS=0")

option(FIREBALL_BUILD_UVWASI "Build the uvwasi platform driver dependency" OFF)

if(FIREBALL_BUILD_UVWASI)
    set(UVWASI_BUILD_SHARED ON CACHE BOOL "" FORCE)
    set(INSTALL_UVWASI OFF CACHE BOOL "" FORCE)
    set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
    set(FETCHCONTENT_SOURCE_DIR_LIBUV
        "${CMAKE_CURRENT_SOURCE_DIR}/third_party/libuv"
        CACHE PATH "Use the Fireball libuv submodule")
    add_subdirectory(third_party/uvwasi third_party/uvwasi-build EXCLUDE_FROM_ALL)
    # uvwasi upstream gives both static and shared targets the same Windows
    # import/archive name. Keep the static artifact distinct so Ninja can
    # generate both targets in one Fireball build.
    set_target_properties(uvwasi_a PROPERTIES OUTPUT_NAME "uvwasi_static")
    if(WIN32)
        # The upstream export annotation is ELF-oriented. Export the public
        # C API from the Windows DLL so the Python ctypes adapter can load it.
        set_target_properties(uvwasi PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
    endif()
endif()
