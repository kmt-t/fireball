/* Shared x86-64 JIT chain dispatcher. */

#if defined(_WIN32)
#define FB_REL_REG r9
#else
#define FB_REL_REG rcx
#endif

#ifndef FB_TRACE_CONST_REL_OFFSET
#error "FB_TRACE_CONST_REL_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_HEADER_BYTES
#error "FB_TRACE_HEADER_BYTES must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_CHAIN_TARGET_OFFSET
#error "FB_TRACE_CHAIN_TARGET_OFFSET must come from the production C++ ABI probe"
#endif

.intel_syntax noprefix
.text

.globl fb_common_chain_dispatcher
#if !defined(_WIN32)
.type fb_common_chain_dispatcher,@function
#endif
fb_common_chain_dispatcher:
  mov rax, qword ptr [FB_REL_REG + FB_TRACE_CHAIN_TARGET_OFFSET]
  test rax, rax
  jz .Lchain_return

  /* chain_target_address is the target body address. Recover its header,
   * resolve the header-relative constant-pool offset, then enter the body. */
  mov r10d, dword ptr [rax - (FB_TRACE_HEADER_BYTES - FB_TRACE_CONST_REL_OFFSET)]
  lea FB_REL_REG, [rax + r10 - FB_TRACE_HEADER_BYTES]
  jmp rax

.Lchain_return:
  ret
#if !defined(_WIN32)
.size fb_common_chain_dispatcher,.-fb_common_chain_dispatcher
#endif
