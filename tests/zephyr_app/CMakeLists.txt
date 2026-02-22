cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(fireball_test)

target_sources(app PRIVATE src/main.cxx)
target_include_directories(app PRIVATE 
    ${CMAKE_CURRENT_SOURCE_DIR}/../../inc
    ${CMAKE_CURRENT_SOURCE_DIR}/../../inc/core
)

# Fireball uses C++20 features
target_compile_options(app PRIVATE -std=c++20)
