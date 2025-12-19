project('fireball', 'c', 'cpp',
  version : '0.1.0',
  default_options : [
    'c_std=c99',
    'cpp_std=c++23',
    'warning_level=3',
    'buildtype=debug',
  ]
)

target = get_option('target_machine')
if target == 'cortex-m33'
  target_flags = [
    '-target', 'arm-none-eabi',
    '-mcpu=cortex-m33',
    '-mthumb',
    '-fno-rtti',
    '-fno-exceptions',
  ]
elif target == 'riscv32'
  target_flags = [
    '-target', 'riscv32-unknown-elf',
    '-march=rv32i',
    '-mabi=ilp32',
    '-fno-rtti',
    '-fno-exceptions',
  ]
else
  target_flags = [
    '-fno-rtti',
    '-fno-exceptions',
  ]
endif

build_type = get_option('buildtype')
if build_type == 'release' or build_type == 'debugoptimized'
  release_args = ['-O3', '-march=native', '-flto']
else
  release_args = ['-g', '-Og']
endif

incdirs = include_directories(
  'inc',
)
srcfiles = files(
  'src/main.cxx',
  'src/utils/backtrace.cxx',
  'src/allocator/malloc.c',
  'src/allocator/stdcxx_allocator.cxx',
)

fireball_exe = executable('fireball',
   srcfiles,
   include_directories : incdirs,
   c_args : target_flags + [
     '-D_POSIX_C_SOURCE=200809L',
     '-DHAVE_MMAP=0',
     '-DHAVE_MORECORE=0',
     '-DUSE_LOCKS=0',
     '-DMSPACES=1',
     '-DFOOTERS=0',
     '-DINSECURE=0',
     '-DNO_MALLOC_STATS=1',
     '-DMMAP_CLEARS=0',
   ] + release_args,
   cpp_args : target_flags + [
     '-D_POSIX_C_SOURCE=200809L',
   ] + release_args,
   link_args : target_flags + ['-lstdc++exp'] + release_args,
   install : true,
)
