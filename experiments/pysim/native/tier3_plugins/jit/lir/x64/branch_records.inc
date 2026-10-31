/* Local direct-branch macros for the JIT stencil generator (REQ-096 stage 2).

   A branch inside a stencil is emitted with a fixed encoding (rel8 for FB_LOCAL_BRANCH_REL8,
   rel32 for FB_LOCAL_BRANCH_REL32) and its position is recorded in .fb_branch_records. A record
   is three little-endian words: the instruction offset, the target offset, and the instruction
   size, all relative to .Ltext_base at the start of .text. The generator reads these records
   instead of disassembling the object, so the fixup table never depends on a tool's text format.
   The source must define .Ltext_base at the start of .text before any branch macro is used. */

.macro FB_BRANCH_RECORD ins, target, size
  .pushsection .fb_branch_records, "a"
  .long \ins - .Ltext_base
  .long \target - .Ltext_base
  .long \size
  .popsection
.endm

.macro FB_LOCAL_BRANCH_REL8 opcode, target
.Lbranch_ins_\@:
  .byte \opcode
.Lbranch_field_\@:
  .byte \target - (.Lbranch_field_\@ + 1)
  FB_BRANCH_RECORD .Lbranch_ins_\@, \target, 2
.endm

.macro FB_LOCAL_BRANCH_REL32 cc, target
.Lbranch_ins_\@:
  .byte 0x0f, \cc
.Lbranch_field_\@:
  .long \target - (.Lbranch_field_\@ + 4)
  FB_BRANCH_RECORD .Lbranch_ins_\@, \target, 6
.endm
