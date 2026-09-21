cmake_minimum_required(VERSION 3.20)
project(fireball)

set(CMAKE_POSITION_INDEPENDENT_CODE OFF)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/inc)

add_executable(fireball
    src/main.cxx
    src/allocator/host_allocator.cxx
    src/allocator/malloc.c
    src/utils/backtrace.cxx
)

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
