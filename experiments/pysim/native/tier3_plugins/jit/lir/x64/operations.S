/* Handwritten x86-64 PIC stencils. Clang's assembler resolves local labels;
 * generate_assembly.py extracts this .text into a C++ byte array. */

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

#ifndef FB_CONTEXT_LOCAL_BASE_OFFSET
#error "FB_CONTEXT_LOCAL_BASE_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_CONTEXT_IP_OFFSET
#error "FB_CONTEXT_IP_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_CONTEXT_SP_OFFSET
#error "FB_CONTEXT_SP_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_COMMON_RELATIVE_OFFSET
#error "FB_TRACE_COMMON_RELATIVE_OFFSET must come from the production C++ ABI probe"
#endif
#ifndef FB_TRACE_RUNTIME_API_TABLE_OFFSET
#error "FB_TRACE_RUNTIME_API_TABLE_OFFSET must come from the common-area layout"
#endif

.intel_syntax noprefix
.text

.macro FB_STENCIL_BEGIN name
  .globl \name
#if !defined(_WIN32)
  .type \name,@function
#endif
\name:
.endm

.macro FB_SLOT_MARK name, opcode
  .globl \name
\name:
  .byte \opcode
  .long 0
.endm

.macro FB_PATCH_SLOT_1 name
  .globl fb_jit_patch_slot_1__\name
fb_jit_patch_slot_1__\name:
  .byte 0
.endm

.macro FB_PATCH_SLOT_4 name
  .globl fb_jit_patch_slot_4__\name
fb_jit_patch_slot_4__\name:
  .long 0
.endm

.macro FB_INSTRUCTION_SLOT name
  .globl fb_jit_instruction_slot__\name
fb_jit_instruction_slot__\name:
  .byte 0x90, 0x90, 0x90, 0x90
.endm

.macro FB_STENCIL_END name
  FB_SLOT_MARK fb_jit_next_slot__\name, 0xe9
#if !defined(_WIN32)
  .size \name,.-\name
#endif
.endm

FB_STENCIL_BEGIN fb_stencil_i32_const
  .byte 0xb8
  FB_PATCH_SLOT_4 fb_stencil_i32_const
  mov dword ptr [FB_SP_REG], eax
  lea FB_SP_REG, [FB_SP_REG + 4]
FB_STENCIL_END fb_stencil_i32_const

FB_STENCIL_BEGIN fb_stencil_f32_const
  .byte 0xb8
  FB_PATCH_SLOT_4 fb_stencil_f32_const
  mov dword ptr [FB_SP_REG], eax
  lea FB_SP_REG, [FB_SP_REG + 4]
FB_STENCIL_END fb_stencil_f32_const

FB_STENCIL_BEGIN fb_stencil_drop
  lea FB_SP_REG, [FB_SP_REG - 4]
FB_STENCIL_END fb_stencil_drop

FB_STENCIL_BEGIN fb_stencil_drop_wide
  lea FB_SP_REG, [FB_SP_REG - 8]
FB_STENCIL_END fb_stencil_drop_wide

FB_STENCIL_BEGIN fb_stencil_select_i32
  mov r10d, dword ptr [FB_SP_REG - 4]
  mov eax, dword ptr [FB_SP_REG - 8]
  mov r11d, dword ptr [FB_SP_REG - 12]
  test r10d, r10d
  cmovne eax, r11d
  mov dword ptr [FB_SP_REG - 12], eax
  lea FB_SP_REG, [FB_SP_REG - 8]
FB_STENCIL_END fb_stencil_select_i32

FB_STENCIL_BEGIN fb_stencil_select_i64
  mov r10d, dword ptr [FB_SP_REG - 4]
  mov rax, qword ptr [FB_SP_REG - 12]
  mov r11, qword ptr [FB_SP_REG - 20]
  test r10d, r10d
  cmovne rax, r11
  mov qword ptr [FB_SP_REG - 20], rax
  lea FB_SP_REG, [FB_SP_REG - 12]
FB_STENCIL_END fb_stencil_select_i64

FB_STENCIL_BEGIN fb_stencil_i32_eqz
  cmp dword ptr [FB_SP_REG - 4], 0
  sete al
  movzx eax, al
  mov dword ptr [FB_SP_REG - 4], eax
FB_STENCIL_END fb_stencil_i32_eqz

.macro FB_I32_BINARY name, operation
  FB_STENCIL_BEGIN \name
    mov eax, dword ptr [FB_SP_REG - 4]
    \operation dword ptr [FB_SP_REG - 8], eax
    lea FB_SP_REG, [FB_SP_REG - 4]
  FB_STENCIL_END \name
.endm

FB_I32_BINARY fb_stencil_i32_add, add
FB_I32_BINARY fb_stencil_i32_sub, sub
FB_I32_BINARY fb_stencil_i32_and, and
FB_I32_BINARY fb_stencil_i32_or, or
FB_I32_BINARY fb_stencil_i32_xor, xor

FB_STENCIL_BEGIN fb_stencil_i32_mul
  mov eax, dword ptr [FB_SP_REG - 4]
  imul eax, dword ptr [FB_SP_REG - 8]
  mov dword ptr [FB_SP_REG - 8], eax
  lea FB_SP_REG, [FB_SP_REG - 4]
FB_STENCIL_END fb_stencil_i32_mul

.macro FB_I32_SHIFT name, operation
  FB_STENCIL_BEGIN \name
    mov r10, FB_REL_REG
    mov ecx, dword ptr [FB_SP_REG - 4]
    and ecx, 31
    \operation dword ptr [FB_SP_REG - 8], cl
    lea FB_SP_REG, [FB_SP_REG - 4]
    mov FB_REL_REG, r10
  FB_STENCIL_END \name
.endm

FB_I32_SHIFT fb_stencil_i32_shl, shl
FB_I32_SHIFT fb_stencil_i32_shr_s, sar
FB_I32_SHIFT fb_stencil_i32_shr_u, shr

.macro FB_I32_COMPARE name, condition
  FB_STENCIL_BEGIN \name
    mov eax, dword ptr [FB_SP_REG - 8]
    cmp eax, dword ptr [FB_SP_REG - 4]
    \condition al
    movzx eax, al
    mov dword ptr [FB_SP_REG - 8], eax
    lea FB_SP_REG, [FB_SP_REG - 4]
  FB_STENCIL_END \name
.endm

FB_I32_COMPARE fb_stencil_i32_eq, sete
FB_I32_COMPARE fb_stencil_i32_ne, setne
FB_I32_COMPARE fb_stencil_i32_lt_s, setl
FB_I32_COMPARE fb_stencil_i32_lt_u, setb
FB_I32_COMPARE fb_stencil_i32_gt_s, setg
FB_I32_COMPARE fb_stencil_i32_gt_u, seta
FB_I32_COMPARE fb_stencil_i32_le_s, setle
FB_I32_COMPARE fb_stencil_i32_le_u, setbe
FB_I32_COMPARE fb_stencil_i32_ge_s, setge
FB_I32_COMPARE fb_stencil_i32_ge_u, setae

.macro FB_I32_IMMEDIATE name, operation
  FB_STENCIL_BEGIN \name
    .byte 0xb8
    FB_PATCH_SLOT_4 \name
    \operation dword ptr [FB_SP_REG - 4], eax
  FB_STENCIL_END \name
.endm

FB_I32_IMMEDIATE fb_stencil_i32_add_immediate, add
FB_I32_IMMEDIATE fb_stencil_i32_sub_immediate, sub
FB_I32_IMMEDIATE fb_stencil_i32_and_immediate, and
FB_I32_IMMEDIATE fb_stencil_i32_or_immediate, or
FB_I32_IMMEDIATE fb_stencil_i32_xor_immediate, xor

FB_STENCIL_BEGIN fb_stencil_i32_mul_immediate
  .byte 0xb8
  FB_PATCH_SLOT_4 fb_stencil_i32_mul_immediate
  imul eax, dword ptr [FB_SP_REG - 4]
  mov dword ptr [FB_SP_REG - 4], eax
FB_STENCIL_END fb_stencil_i32_mul_immediate

.macro FB_I32_IMMEDIATE_SHIFT name, group
  FB_STENCIL_BEGIN \name
#if defined(_WIN32)
    .byte 0x41, 0xc1, (0x40 + (\group * 8)), 0xfc
#else
    .byte 0xc1, (0x42 + (\group * 8)), 0xfc
#endif
    FB_PATCH_SLOT_1 \name
  FB_STENCIL_END \name
.endm

FB_I32_IMMEDIATE_SHIFT fb_stencil_i32_shl_immediate, 4
FB_I32_IMMEDIATE_SHIFT fb_stencil_i32_shr_s_immediate, 7
FB_I32_IMMEDIATE_SHIFT fb_stencil_i32_shr_u_immediate, 5

.macro FB_LOCAL_GET_NARROW type
  FB_STENCIL_BEGIN fb_stencil_local_get_\type
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    FB_INSTRUCTION_SLOT fb_stencil_local_get_\type
    mov dword ptr [FB_SP_REG], eax
    lea FB_SP_REG, [FB_SP_REG + 4]
  FB_STENCIL_END fb_stencil_local_get_\type
.endm

.macro FB_LOCAL_SET_NARROW type
  FB_STENCIL_BEGIN fb_stencil_local_set_\type
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    mov eax, dword ptr [FB_SP_REG - 4]
    FB_INSTRUCTION_SLOT fb_stencil_local_set_\type
    lea FB_SP_REG, [FB_SP_REG - 4]
  FB_STENCIL_END fb_stencil_local_set_\type
.endm

.macro FB_LOCAL_TEE_NARROW type
  FB_STENCIL_BEGIN fb_stencil_local_tee_\type
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    mov eax, dword ptr [FB_SP_REG - 4]
    FB_INSTRUCTION_SLOT fb_stencil_local_tee_\type
  FB_STENCIL_END fb_stencil_local_tee_\type
.endm

.macro FB_LOCAL_GET_WIDE type
  FB_STENCIL_BEGIN fb_stencil_local_get_\type
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    FB_INSTRUCTION_SLOT fb_stencil_local_get_\type
    mov qword ptr [FB_SP_REG], rax
    lea FB_SP_REG, [FB_SP_REG + 8]
  FB_STENCIL_END fb_stencil_local_get_\type
.endm

.macro FB_LOCAL_SET_WIDE type
  FB_STENCIL_BEGIN fb_stencil_local_set_\type
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    mov rax, qword ptr [FB_SP_REG - 8]
    FB_INSTRUCTION_SLOT fb_stencil_local_set_\type
    lea FB_SP_REG, [FB_SP_REG - 8]
  FB_STENCIL_END fb_stencil_local_set_\type
.endm

.macro FB_LOCAL_TEE_WIDE type
  FB_STENCIL_BEGIN fb_stencil_local_tee_\type
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    mov rax, qword ptr [FB_SP_REG - 8]
    FB_INSTRUCTION_SLOT fb_stencil_local_tee_\type
  FB_STENCIL_END fb_stencil_local_tee_\type
.endm

FB_LOCAL_GET_NARROW i32
FB_LOCAL_SET_NARROW i32
FB_LOCAL_TEE_NARROW i32
FB_LOCAL_GET_WIDE i64
FB_LOCAL_SET_WIDE i64
FB_LOCAL_TEE_WIDE i64
FB_LOCAL_GET_NARROW f32
FB_LOCAL_SET_NARROW f32
FB_LOCAL_TEE_NARROW f32
FB_LOCAL_GET_WIDE f64
FB_LOCAL_SET_WIDE f64
FB_LOCAL_TEE_WIDE f64

/* Call one opcode API through the common-area table; false exits the trace. */
.macro FB_RUNTIME_API_STENCIL name, rel_advance
FB_STENCIL_BEGIN \name
  movsxd r10, dword ptr [FB_REL_REG]
  lea r11, [FB_REL_REG + r10]
  mov eax, dword ptr [FB_REL_REG + 4]
  mov r10d, dword ptr [FB_REL_REG + 8]
  mov dword ptr [FB_CTX_REG + FB_CONTEXT_IP_OFFSET], r10d
  movsxd r10, dword ptr [FB_REL_REG + 12]
  shl r10, 2
  add qword ptr [FB_CTX_REG + FB_CONTEXT_SP_OFFSET], r10
  movsxd r10, dword ptr [r11 + FB_TRACE_COMMON_RELATIVE_OFFSET]
  lea r11, [r11 + r10 + FB_TRACE_RUNTIME_API_TABLE_OFFSET]
  mov rax, qword ptr [r11 + rax * 8]
  mov r10d, dword ptr [FB_REL_REG + 16]
  push FB_CTX_REG
  push FB_IP_REG
  push FB_SP_REG
  push FB_REL_REG
  sub rsp, 64
  mov qword ptr [rsp + 32], FB_SP_REG
  mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
  mov qword ptr [rsp + 40], r11
  mov dword ptr [rsp + 48], r10d
#if defined(_WIN32)
  mov rcx, qword ptr [rsp + 88]
  lea rdx, [rsp + 32]
  lea r8, [rsp + 40]
  lea r9, [FB_REL_REG + 20]
#else
  mov rdi, qword ptr [rsp + 88]
  lea rsi, [rsp + 32]
  lea rdx, [rsp + 40]
  lea rcx, [FB_REL_REG + 20]
#endif
  call rax
  mov r11, qword ptr [rsp + 88]
  mov r10, qword ptr [rsp + 40]
  mov qword ptr [r11 + FB_CONTEXT_LOCAL_BASE_OFFSET], r10
  test al, al
  sete r8b
  movzx r8d, r8b
  test r8d, r8d
  jnz .Lruntime_api_exit\@
  movsxd r10, dword ptr [rsp + 48]
  shl r10, 2
  sub qword ptr [r11 + FB_CONTEXT_SP_OFFSET], r10
.Lruntime_api_exit\@:
  mov r10, qword ptr [rsp + 32]
  mov r11, qword ptr [rsp + 64]
  add r11, \rel_advance
  add rsp, 64
  pop FB_REL_REG
  pop FB_SP_REG
  pop FB_IP_REG
  pop FB_CTX_REG
  mov FB_SP_REG, r10
  mov FB_REL_REG, r11
  test r8d, r8d
  jnz .Lruntime_api_abort\@
  jmp .Lruntime_api_continue\@
.Lruntime_api_abort\@:
  FB_SLOT_MARK fb_jit_abort_slot__\name, 0xe9
.Lruntime_api_continue\@:
  FB_STENCIL_END \name
.endm

FB_RUNTIME_API_STENCIL fb_stencil_runtime_api, 20
FB_RUNTIME_API_STENCIL fb_stencil_runtime_api_immediate_one, 24
FB_RUNTIME_API_STENCIL fb_stencil_runtime_api_immediate_two, 28
FB_RUNTIME_API_STENCIL fb_stencil_runtime_api_immediate_three, 32

.macro FB_LOCAL_SET_CONST_I32
  FB_STENCIL_BEGIN fb_stencil_local_set_const_i32
    .byte 0xb8
    FB_PATCH_SLOT_4 fb_stencil_local_set_const_i32
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    FB_INSTRUCTION_SLOT fb_stencil_local_set_const_i32
  FB_STENCIL_END fb_stencil_local_set_const_i32
.endm

FB_LOCAL_SET_CONST_I32

.macro FB_FUSED_BINARY name, operation
  FB_STENCIL_BEGIN fb_stencil_i32_\name\()_local
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    FB_INSTRUCTION_SLOT fb_stencil_i32_\name\()_local
    \operation dword ptr [FB_SP_REG - 4], eax
  FB_STENCIL_END fb_stencil_i32_\name\()_local
.endm

.macro FB_FUSED_MUL
  FB_STENCIL_BEGIN fb_stencil_i32_mul_local
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    FB_INSTRUCTION_SLOT fb_stencil_i32_mul_local
    imul eax, dword ptr [FB_SP_REG - 4]
    mov dword ptr [FB_SP_REG - 4], eax
  FB_STENCIL_END fb_stencil_i32_mul_local
.endm

.macro FB_FUSED_COMPARE name, condition
  FB_STENCIL_BEGIN fb_stencil_i32_\name\()_local
    mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
    FB_INSTRUCTION_SLOT fb_stencil_i32_\name\()_local
    cmp dword ptr [FB_SP_REG - 4], eax
    \condition al
    movzx eax, al
    mov dword ptr [FB_SP_REG - 4], eax
  FB_STENCIL_END fb_stencil_i32_\name\()_local
.endm

FB_FUSED_BINARY add,add
FB_FUSED_BINARY sub,sub
FB_FUSED_MUL
FB_FUSED_BINARY and,and
FB_FUSED_BINARY or,or
FB_FUSED_BINARY xor,xor
FB_FUSED_COMPARE eq,sete
FB_FUSED_COMPARE ne,setne
FB_FUSED_COMPARE lt_s,setl
FB_FUSED_COMPARE lt_u,setb
FB_FUSED_COMPARE gt_s,setg
FB_FUSED_COMPARE gt_u,seta
FB_FUSED_COMPARE le_s,setle
FB_FUSED_COMPARE le_u,setbe
FB_FUSED_COMPARE ge_s,setge
FB_FUSED_COMPARE ge_u,setae
