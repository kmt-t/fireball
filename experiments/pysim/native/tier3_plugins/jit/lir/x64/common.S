/* Shared x86-64 JIT chain dispatcher. */

#if defined(_WIN32)
#define FB_REL_REG r9
#else
#define FB_REL_REG rcx
#endif
#define FB_CTX_REG r12

#ifndef FB_TRACE_CONST_REL_OFFSET
#error "FB_TRACE_CONST_REL_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_HEADER_BYTES
#error "FB_TRACE_HEADER_BYTES must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_CHAIN_TARGET_OFFSET
#error "FB_TRACE_CHAIN_TARGET_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_CHAIN_ENTRY_OFFSET
#error "FB_TRACE_CHAIN_ENTRY_OFFSET must come from tier1_core/config.py"
#endif
#ifndef FB_TRACE_BOUNDARY_METADATA_BYTES
#error "FB_TRACE_BOUNDARY_METADATA_BYTES must come from the production C++ ABI probe"
#endif
#ifndef FB_CONTEXT_IP_OFFSET
#error "FB_CONTEXT_IP_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_CONTEXT_REL_OFFSET
#error "FB_CONTEXT_REL_OFFSET must come from the production C++ ABI probe"
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
  mov qword ptr [FB_CTX_REG + FB_CONTEXT_REL_OFFSET], FB_REL_REG
  add FB_REL_REG, 4
  add rax, FB_TRACE_CHAIN_ENTRY_OFFSET
  jmp rax

.Lchain_return:
  /* Only the unlinked terminal path publishes its footer and terminal opcode
   * PC. The bool return marks normal completion; lookup never walks the chain. */
  mov eax, dword ptr [FB_REL_REG + FB_TRACE_CONST_REL_OFFSET]
  lea rax, [FB_REL_REG + rax - FB_TRACE_BOUNDARY_METADATA_BYTES]
  mov qword ptr [FB_CTX_REG + FB_CONTEXT_REL_OFFSET], rax
  mov edx, dword ptr [rax]
  mov dword ptr [FB_CTX_REG + FB_CONTEXT_IP_OFFSET], edx
  add rsp, 8
  pop r12
  mov eax, 1
  ret
#if !defined(_WIN32)
.size fb_common_chain_dispatcher,.-fb_common_chain_dispatcher
#endif
