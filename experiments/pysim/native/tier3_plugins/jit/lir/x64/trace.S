/* Handwritten x86-64 arithmetic and trace-entry stencils. */

#if defined(_WIN32)
#define FB_CTX_REG rcx
#define FB_IP_REG rdx
#define FB_SP_REG r8
#define FB_REL_REG r9
#else
#define FB_CTX_REG rdi
#define FB_IP_REG rsi
#define FB_SP_REG rdx
#define FB_REL_REG rcx
#endif

#ifndef FB_CONTEXT_TRAP_CODE_OFFSET
#error "FB_CONTEXT_TRAP_CODE_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_COMMON_RELATIVE_OFFSET
#error "FB_TRACE_COMMON_RELATIVE_OFFSET must come from the production C++ ABI probe"
#endif

.intel_syntax noprefix
.text

.macro FB_SLOT_MARK name, opcode
  .globl \name
\name:
  .byte \opcode
  .long 0
.endm

.macro FB_PATCH_SLOT_8 name
  .globl fb_jit_patch_slot_8__\name
fb_jit_patch_slot_8__\name:
  .quad 0
.endm

.macro FB_TRACE_BEGIN name
  .globl \name
#if !defined(_WIN32)
  .type \name,@function
#endif
\name:
.endm

.macro FB_TRACE_END name
#if !defined(_WIN32)
  .size \name,.-\name
#endif
.endm

.macro FB_NEXT_MARK name
  FB_SLOT_MARK fb_jit_next_slot__\name, 0xe9
.endm

FB_TRACE_BEGIN fb_stencil_i64_const
  .byte 0x48, 0xb8
  FB_PATCH_SLOT_8 fb_stencil_i64_const
  mov qword ptr [FB_SP_REG], rax
  lea FB_SP_REG, [FB_SP_REG + 8]
  FB_NEXT_MARK fb_stencil_i64_const
FB_TRACE_END fb_stencil_i64_const

FB_TRACE_BEGIN fb_stencil_f64_const
  .byte 0x48, 0xb8
  FB_PATCH_SLOT_8 fb_stencil_f64_const
  mov qword ptr [FB_SP_REG], rax
  lea FB_SP_REG, [FB_SP_REG + 8]
  FB_NEXT_MARK fb_stencil_f64_const
FB_TRACE_END fb_stencil_f64_const

FB_TRACE_BEGIN fb_stencil_i64_add
  mov rax, qword ptr [FB_SP_REG - 8]
  add qword ptr [FB_SP_REG - 16], rax
  lea FB_SP_REG, [FB_SP_REG - 8]
  FB_NEXT_MARK fb_stencil_i64_add
FB_TRACE_END fb_stencil_i64_add

FB_TRACE_BEGIN fb_stencil_i64_sub
  mov rax, qword ptr [FB_SP_REG - 8]
  sub qword ptr [FB_SP_REG - 16], rax
  lea FB_SP_REG, [FB_SP_REG - 8]
  FB_NEXT_MARK fb_stencil_i64_sub
FB_TRACE_END fb_stencil_i64_sub

FB_TRACE_BEGIN fb_stencil_i64_mul
  mov rax, qword ptr [FB_SP_REG - 8]
  imul rax, qword ptr [FB_SP_REG - 16]
  mov qword ptr [FB_SP_REG - 16], rax
  lea FB_SP_REG, [FB_SP_REG - 8]
  FB_NEXT_MARK fb_stencil_i64_mul
FB_TRACE_END fb_stencil_i64_mul

.macro FB_F32_BINARY name, operation
  FB_TRACE_BEGIN fb_stencil_f32_\name
    movss xmm0, dword ptr [FB_SP_REG - 4]
    movss xmm1, dword ptr [FB_SP_REG - 8]
    \operation xmm1, xmm0
    movss dword ptr [FB_SP_REG - 8], xmm1
    lea FB_SP_REG, [FB_SP_REG - 4]
    FB_NEXT_MARK fb_stencil_f32_\name
  FB_TRACE_END fb_stencil_f32_\name
.endm

FB_F32_BINARY add, addss
FB_F32_BINARY sub, subss
FB_F32_BINARY mul, mulss
FB_F32_BINARY div, divss

.macro FB_F64_BINARY name, operation
  FB_TRACE_BEGIN fb_stencil_f64_\name
    movsd xmm0, qword ptr [FB_SP_REG - 8]
    movsd xmm1, qword ptr [FB_SP_REG - 16]
    \operation xmm1, xmm0
    movsd qword ptr [FB_SP_REG - 16], xmm1
    lea FB_SP_REG, [FB_SP_REG - 8]
    FB_NEXT_MARK fb_stencil_f64_\name
  FB_TRACE_END fb_stencil_f64_\name
.endm

FB_F64_BINARY add, addsd
FB_F64_BINARY sub, subsd
FB_F64_BINARY mul, mulsd
FB_F64_BINARY div, divsd

FB_TRACE_BEGIN fb_stencil_i32_div_s
  mov FB_IP_REG, FB_SP_REG
  mov r10d, dword ptr [FB_SP_REG - 4]
  mov r11d, 15
  test r10d, r10d
  jz .Ldiv_s_trap
  mov eax, dword ptr [FB_SP_REG - 8]
  cmp eax, 0x80000000
  jne .Ldiv_s_normal
  cmp r10d, -1
  jne .Ldiv_s_normal
  mov r11d, 16
.Ldiv_s_trap:
  mov dword ptr [FB_CTX_REG + FB_CONTEXT_TRAP_CODE_OFFSET], r11d
  FB_SLOT_MARK fb_jit_abort_slot__fb_stencil_i32_div_s, 0xe9
.Ldiv_s_normal:
  mov eax, dword ptr [FB_IP_REG - 8]
  cdq
  idiv r10d
  mov dword ptr [FB_IP_REG - 8], eax
  lea FB_SP_REG, [FB_IP_REG - 4]
  FB_NEXT_MARK fb_stencil_i32_div_s
FB_TRACE_END fb_stencil_i32_div_s

FB_TRACE_BEGIN fb_stencil_i32_div_u
  mov FB_IP_REG, FB_SP_REG
  mov r10d, dword ptr [FB_SP_REG - 4]
  test r10d, r10d
  jnz .Ldiv_u_normal
  mov dword ptr [FB_CTX_REG + FB_CONTEXT_TRAP_CODE_OFFSET], 15
  FB_SLOT_MARK fb_jit_abort_slot__fb_stencil_i32_div_u, 0xe9
.Ldiv_u_normal:
  mov eax, dword ptr [FB_IP_REG - 8]
  xor edx, edx
  div r10d
  mov dword ptr [FB_IP_REG - 8], eax
  lea FB_SP_REG, [FB_IP_REG - 4]
  FB_NEXT_MARK fb_stencil_i32_div_u
FB_TRACE_END fb_stencil_i32_div_u

FB_TRACE_BEGIN fb_stencil_i32_rem_s
  mov FB_IP_REG, FB_SP_REG
  mov r10d, dword ptr [FB_SP_REG - 4]
  test r10d, r10d
  jnz .Lrem_s_check_overflow
  mov dword ptr [FB_CTX_REG + FB_CONTEXT_TRAP_CODE_OFFSET], 15
  FB_SLOT_MARK fb_jit_abort_slot__fb_stencil_i32_rem_s, 0xe9
.Lrem_s_check_overflow:
  mov eax, dword ptr [FB_IP_REG - 8]
  cmp eax, 0x80000000
  jne .Lrem_s_normal
  cmp r10d, -1
  jne .Lrem_s_normal
  mov dword ptr [FB_IP_REG - 8], 0
  jmp .Lrem_s_done
.Lrem_s_normal:
  mov eax, dword ptr [FB_IP_REG - 8]
  cdq
  idiv r10d
  mov dword ptr [FB_IP_REG - 8], edx
.Lrem_s_done:
  lea FB_SP_REG, [FB_IP_REG - 4]
  FB_NEXT_MARK fb_stencil_i32_rem_s
FB_TRACE_END fb_stencil_i32_rem_s

FB_TRACE_BEGIN fb_stencil_i32_rem_u
  mov FB_IP_REG, FB_SP_REG
  mov r10d, dword ptr [FB_SP_REG - 4]
  test r10d, r10d
  jnz .Lrem_u_normal
  mov dword ptr [FB_CTX_REG + FB_CONTEXT_TRAP_CODE_OFFSET], 15
  FB_SLOT_MARK fb_jit_abort_slot__fb_stencil_i32_rem_u, 0xe9
.Lrem_u_normal:
  mov eax, dword ptr [FB_IP_REG - 8]
  xor edx, edx
  div r10d
  mov dword ptr [FB_IP_REG - 8], edx
  lea FB_SP_REG, [FB_IP_REG - 4]
  FB_NEXT_MARK fb_stencil_i32_rem_u
FB_TRACE_END fb_stencil_i32_rem_u

FB_TRACE_BEGIN fb_trace_exit
  movsxd r10, dword ptr [FB_REL_REG]
  lea r11, [FB_REL_REG + r10]
  movsxd r10, dword ptr [r11 + FB_TRACE_COMMON_RELATIVE_OFFSET]
  lea rax, [r11 + r10]
  mov FB_REL_REG, r11
  jmp rax
FB_TRACE_END fb_trace_exit

FB_TRACE_BEGIN fb_trace_entry
  FB_SLOT_MARK fb_jit_start_slot__fb_trace_entry, 0xe8
  movq r10, xmm0
  test r10b, 1
  jnz .Ltrace_entry_success
  ret
.Ltrace_entry_success:
  pshufd xmm1, xmm0, 238
  movq FB_REL_REG, xmm1
  and r10, -2
  mov FB_SP_REG, r10
  FB_SLOT_MARK fb_jit_exit_slot__fb_trace_entry, 0xe9
FB_TRACE_END fb_trace_entry

FB_TRACE_BEGIN fb_trace_return_success
  or FB_SP_REG, 1
  movq xmm0, FB_SP_REG
  movq xmm1, FB_REL_REG
  punpcklqdq xmm0, xmm1
  ret
FB_TRACE_END fb_trace_return_success

FB_TRACE_BEGIN fb_trace_return_abort
  movq xmm0, FB_SP_REG
  movq xmm1, FB_REL_REG
  punpcklqdq xmm0, xmm1
  ret
FB_TRACE_END fb_trace_return_abort
