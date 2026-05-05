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
