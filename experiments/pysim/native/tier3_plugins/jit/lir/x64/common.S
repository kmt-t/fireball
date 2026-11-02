/* Shared x86-64 JIT chain dispatcher. */

#if defined(_WIN32)
#define FB_REL_REG r9
#define FB_ENTRY_CTX_REG rcx
#else
#define FB_REL_REG rcx
#define FB_ENTRY_CTX_REG rdi
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
#ifndef FB_TRACE_COMMON_ENTRY_OFFSET
#error "FB_TRACE_COMMON_ENTRY_OFFSET must come from tier1_core/config.py"
#endif
#ifndef FB_CONTEXT_TRAP_CODE_OFFSET
#error "FB_CONTEXT_TRAP_CODE_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_CONTEXT_RUNTIME_FLAGS_OFFSET
#error "FB_CONTEXT_RUNTIME_FLAGS_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_CPS_EXIT_MASK
#error "FB_CPS_EXIT_MASK must come from the production C++ ABI probe"
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
#ifndef FB_CONTEXT_CURRENT_BLOCK_INDEX_OFFSET
#error "FB_CONTEXT_CURRENT_BLOCK_INDEX_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_BLOCK_INDEX_OFFSET
#error "FB_TRACE_BLOCK_INDEX_OFFSET must come from the production C++ ABI probe"
#endif

.intel_syntax noprefix
.text

.globl fb_common_chain_dispatcher
#if !defined(_WIN32)
.type fb_common_chain_dispatcher,@function
#endif
.Lchain_dispatcher:
fb_common_chain_dispatcher:
  mov rax, qword ptr [FB_REL_REG + FB_TRACE_CHAIN_TARGET_OFFSET]
  test rax, rax
  jz .Lchain_return

  /* A direct edge enters a different loader block without an Interpreter
   * boundary, so publish its static block index before the tail jump. */
  movzx r10d, word ptr [rax - (FB_TRACE_HEADER_BYTES - FB_TRACE_BLOCK_INDEX_OFFSET)]
  cmp r10w, -1
  je .Lchain_keep_block_index
  mov word ptr [FB_CTX_REG + FB_CONTEXT_CURRENT_BLOCK_INDEX_OFFSET], r10w
.Lchain_keep_block_index:

  /* chain_target_address is the target body address. Recover its header,
   * resolve the header-relative constant-pool offset, then enter the body. */
  mov r10d, dword ptr [rax - (FB_TRACE_HEADER_BYTES - FB_TRACE_CONST_REL_OFFSET)]
  lea FB_REL_REG, [rax + r10 - FB_TRACE_HEADER_BYTES]
  mov qword ptr [FB_CTX_REG + FB_CONTEXT_REL_OFFSET], FB_REL_REG
  add FB_REL_REG, 4
  jmp fb_common_trace_call

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

/* Shared trace entry. The caller passes the trace pool anchor in the rel register. The
 * body starts at header + FB_TRACE_HEADER_BYTES, and the pool anchor holds the
 * header-relative offset in its first four bytes. The dispatcher enters at fb_common_trace_call
 * with rax set to the body and rel set to the anchor after its first four bytes. */
.org FB_TRACE_COMMON_ENTRY_OFFSET
.globl fb_common_trace_entry
#if !defined(_WIN32)
.type fb_common_trace_entry,@function
#endif
fb_common_trace_entry:
  push r12
  sub rsp, 8
  mov r12, FB_ENTRY_CTX_REG
  mov qword ptr [FB_CTX_REG + FB_CONTEXT_REL_OFFSET], FB_REL_REG
  movsxd rax, dword ptr [FB_REL_REG]
  lea rax, [FB_REL_REG + rax + FB_TRACE_HEADER_BYTES]
  add FB_REL_REG, 4
fb_common_trace_call:
  call rax
  /* A trap or a CPS exit stores its reason in execution_context before the body returns. */
  cmp dword ptr [FB_CTX_REG + FB_CONTEXT_TRAP_CODE_OFFSET], 0
  jne .Ltrace_abort
  test dword ptr [FB_CTX_REG + FB_CONTEXT_RUNTIME_FLAGS_OFFSET], FB_CPS_EXIT_MASK
  jne .Ltrace_abort
  mov FB_REL_REG, qword ptr [FB_CTX_REG + FB_CONTEXT_REL_OFFSET]
  movsxd r10, dword ptr [FB_REL_REG]
  add FB_REL_REG, r10
  /* Local alias: a global jump would leave a PLT relocation in the PIC stencil object. */
  jmp .Lchain_dispatcher
.Ltrace_abort:
  xor eax, eax
  add rsp, 8
  pop r12
  ret
#if !defined(_WIN32)
.size fb_common_trace_entry,.-fb_common_trace_entry
#endif
