project('fireball', 'c', 'cpp',
  version : '0.1.0',
  default_options : [
    'c_std=c99',
    'cpp_std=c++20',
    'warning_level=3',
    'buildtype=debug',
  ]
)

target = get_option('fireball_target')
if target == 'cortex-m33'
  target_flags = [
    '-target', 'arm-none-eabi',
    '-mcpu=cortex-m33',
    '-mthumb',
    '-fno-rtti',
    '-fno-exceptions',
  ]
  qemu_extra_src = files('tools/qemu_startup.s')
  target_link_flags = ['-T', meson.project_source_root() / 'tools/qemu_arm.ld']
elif target == 'riscv32'
  target_flags = [
    '-target', 'riscv32-unknown-elf',
    '-march=rv32i',
    '-mabi=ilp32',
    '-fno-rtti',
    '-fno-exceptions',
  ]
  qemu_extra_src = []
  target_link_flags = []
else
  target_flags = [
    '-fno-rtti',
    '-fno-exceptions',
  ]
  qemu_extra_src = []
  target_link_flags = []
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
  'src/allocator/malloc.c',
  'src/allocator/host_allocator.cxx',
  'src/utils/backtrace.cxx',
)

common_cpp_args = target_flags + [
  '-D_POSIX_C_SOURCE=200809L',
] + release_args

fireball_exe = executable('fireball',
   srcfiles + qemu_extra_src,
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
    cpp_args : common_cpp_args,
   link_args : target_flags + ['-lstdc++exp'] + release_args + target_link_flags,
   install : true,
)

test_constexpr_exe = executable('test_constexpr',
  files('tests/test_constexpr.cxx') + qemu_extra_src,
  include_directories : incdirs,
  cpp_args : common_cpp_args,
  link_args : target_flags + ['-lstdc++exp'] + release_args + target_link_flags,
)

test('constexpr_test', test_constexpr_exe)
