#pragma once
#include "fireball_hostcall.hxx"

#if !defined(__clang__) || __clang_major__ < 17
#error "Fireball requires Clang 17 or later"
#endif

namespace fireball {
inline u32 fireball_call0(u32 syscall_id) {
  return fireball_call(syscall_id, 0, 0, 0, 0, 0, 0);
}
inline u32 fireball_call1(u32 syscall_id, u32 arg0) {
  return fireball_call(syscall_id, arg0, 0, 0, 0, 0, 0);
}
inline u32 fireball_call2(u32 syscall_id, u32 arg0, u32 arg1) {
  return fireball_call(syscall_id, arg0, arg1, 0, 0, 0, 0);
}
inline u32 fireball_call3(u32 syscall_id, u32 arg0, u32 arg1, u32 arg2) {
  return fireball_call(syscall_id, arg0, arg1, arg2, 0, 0, 0);
}
inline u32 fireball_call4(u32 syscall_id, u32 arg0, u32 arg1, u32 arg2, u32 arg3) {
  return fireball_call(syscall_id, arg0, arg1, arg2, arg3, 0, 0);
}
inline u32 fireball_call5(u32 syscall_id, u32 arg0, u32 arg1, u32 arg2, u32 arg3, u32 arg4) {
  return fireball_call(syscall_id, arg0, arg1, arg2, arg3, arg4, 0);
}
inline u32 fireball_call6(
    u32 syscall_id,
    u32 arg0,
    u32 arg1,
    u32 arg2,
    u32 arg3,
    u32 arg4,
    u32 arg5) {
  return fireball_call(syscall_id, arg0, arg1, arg2, arg3, arg4, arg5);
}

inline u32 fireball_virq_register(u32 node_id, u32 function_index) {
  return virq_register(node_id, function_index);
}
inline u32 fireball_virq_unregister(u32 node_id) {
  return virq_unregister(node_id);
}
inline u32 fireball_vdma_start(u32 source, u32 destination, u32 byte_count) {
  return vdma_start(source, destination, byte_count);
}
}
