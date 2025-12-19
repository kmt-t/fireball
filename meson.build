project('fireball', 'c', 'cpp',
  version : '0.1.0',
  default_options : [
    'cpp_std=c++23',
    'warning_level=3',
    'buildtype=debug',
  ]
)

# Release build configuration
release_args = []
if get_option('buildtype') == 'release' or get_option('buildtype') == 'debugoptimized'
  release_args = ['-O3', '-march=native', '-flto']
endif

incdir = include_directories('inc')
src = files(
  'src/main.cxx',
  'src/utils/backtrace.cxx',
  'src/allocator/malloc.c',
  'src/allocator/stdcxx_allocator.cxx',
)

fireball_exe = executable('fireball',
   src,
   include_directories : incdir,
   c_args : [
     '-DHAVE_MMAP=0',
     '-DHAVE_MORECORE=0',
     '-DUSE_LOCKS=0',
     '-DMSPACES=1',
     '-DFOOTERS=0',
     '-DINSECURE=0',
     '-DNO_MALLOC_STATS=1',
     '-DMMAP_CLEARS=0',
   ] + release_args,
   cpp_args : release_args,
   link_args : ['-lstdc++exp'] + release_args,
   install : true,
)
