"""
experiments/pysim/wasm_opcodes.py

The WASM MVP opcode subset this experiment's interpreter and JIT both
compile against. Kept as one shared table so the two engines can never
silently disagree about what a byte means.
"""

UNREACHABLE = 0x00
NOP = 0x01
BLOCK = 0x02
LOOP = 0x03
IF = 0x04
ELSE = 0x05
END = 0x0B
BR = 0x0C
BR_IF = 0x0D
BR_TABLE = 0x0E
RETURN = 0x0F
CALL = 0x10
CALL_INDIRECT = 0x11
DROP = 0x1A
SELECT = 0x1B

LOCAL_GET = 0x20
LOCAL_SET = 0x21
LOCAL_TEE = 0x22
GLOBAL_GET = 0x23
GLOBAL_SET = 0x24

I32_LOAD = 0x28
I32_LOAD8_S = 0x2C
I32_LOAD8_U = 0x2D
I32_LOAD16_S = 0x2E
I32_LOAD16_U = 0x2F
I32_STORE = 0x36
I32_STORE8 = 0x3A
I32_STORE16 = 0x3B
MEMORY_SIZE = 0x3F
MEMORY_GROW = 0x40

I32_CONST = 0x41

I32_EQZ = 0x45
I32_EQ = 0x46
I32_NE = 0x47
I32_LT_S = 0x48
I32_LT_U = 0x49
I32_GT_S = 0x4A
I32_GT_U = 0x4B
I32_LE_S = 0x4C
I32_LE_U = 0x4D
I32_GE_S = 0x4E
I32_GE_U = 0x4F

I32_CLZ = 0x67
I32_CTZ = 0x68
I32_POPCNT = 0x69

I32_ADD = 0x6A
I32_SUB = 0x6B
I32_MUL = 0x6C
I32_DIV_S = 0x6D
I32_DIV_U = 0x6E
I32_REM_S = 0x6F
I32_REM_U = 0x70
I32_AND = 0x71
I32_OR = 0x72
I32_XOR = 0x73
I32_SHL = 0x74
I32_SHR_S = 0x75
I32_SHR_U = 0x76
I32_ROTL = 0x77
I32_ROTR = 0x78

# blocktype byte used by block/loop/if when there is no result value (the
# only form this experiment supports -- multi-value blocktypes are not
# implemented).
BLOCKTYPE_EMPTY = 0x40
