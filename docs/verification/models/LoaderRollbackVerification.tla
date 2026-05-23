--------------------------------------------------------------------------------
-- WASM Loader ロールバック機構・バンプアロケータ整合性の形式検証
-- モジュールのライフサイクル、パース失敗時の安全な巻き戻し、
-- LIFO メモリ制約を検証
-- {ROMParsing} {BumpAllocator} {MultiModule_Support}
--------------------------------------------------------------------------------

---- MODULE LoaderRollbackVerification ----
EXTENDS Integers, Sequences, FiniteSets

-- 定数
CONSTANT MAX_MODULES, MAX_FUNCTIONS_PER_MODULE, ALLOCATOR_SIZE
CONSTANT MAX_IMPORTS
ASSUME MAX_MODULES = 4
ASSUME MAX_FUNCTIONS_PER_MODULE = 256
ASSUME ALLOCATOR_SIZE = 1024
ASSUME MAX_IMPORTS = 32

-- モジュール状態定義
CONSTANT IDLE, PARSING, VERIFYING, READY, ERROR

-- モジュール内の部分的なロード状態
CONSTANT HEADER_PARSED, SECTIONS_SCANNED, EXPORTS_INDEXED, IMPORTS_RESOLVED

-- 変数
VARIABLES
    modules,            -- Module state [module_id] -> {state, parsed_bytes, exports, imports}
    allocator,          -- Bump allocator (LIFO stack) [offset] -> {owner_module, size, timestamp}
    alloc_ptr,          -- Current allocation pointer (0 to ALLOCATOR_SIZE)
    import_table,       -- Unresolved imports [module_id][import_id] -> {module_name, export_name, resolved}
    error_state,        -- Error information {module_id, parse_offset, reason}
    load_order          -- Order of module loading for LIFO validation

vars == <<modules, allocator, alloc_ptr, import_table, error_state, load_order>>

-- 初期状態
Init ==
    /\ modules = [m \in 0..MAX_MODULES-1 |-> [state |-> IDLE, parsed_bytes |-> 0,
                                                exports |-> <<>>, imports |-> <<>>]]
    /\ allocator = [i \in 0..ALLOCATOR_SIZE-1 |-> [owner_module |-> -1, size |-> 0, timestamp |-> 0]]
    /\ alloc_ptr = 0
    /\ import_table = [m \in 0..MAX_MODULES-1 |-> [i \in 0..MAX_IMPORTS-1 |->
                       [module_name |-> "", export_name |-> "", resolved |-> FALSE]]]
    /\ error_state = [module_id |-> -1, parse_offset |-> 0, reason |-> ""]
    /\ load_order = <<>>

-- ========== Phase 1: Prepare（パース準備）==========
Prepare(module_id, binary_size) ==
    LET module == modules[module_id]
    IN /\ module.state = IDLE
       /\ binary_size > 0
       /\ binary_size <= ALLOCATOR_SIZE - alloc_ptr  -- アロケータに空きあり
       /\ modules' = [modules EXCEPT ![module_id].state = PARSING]
       /\ UNCHANGED <<allocator, alloc_ptr, import_table, error_state, load_order>>

-- ========== Phase 2a: Parse（パース処理）==========
Parse(module_id, bytes_to_parse) ==
    LET module == modules[module_id]
    IN /\ module.state = PARSING
       /\ bytes_to_parse > 0
       /\ alloc_ptr + bytes_to_parse <= ALLOCATOR_SIZE
       /\ LET new_ptr == alloc_ptr + bytes_to_parse
          IN /\ allocator' = [allocator EXCEPT ![alloc_ptr] =
                   [owner_module |-> module_id, size |-> bytes_to_parse, timestamp |-> 0]]
             /\ alloc_ptr' = new_ptr
             /\ modules' = [modules EXCEPT ![module_id].parsed_bytes = bytes_to_parse]
       /\ load_order' = Append(load_order, module_id)
       /\ UNCHANGED <<import_table, error_state>>

-- ========== Phase 2b: Verify（検証処理）==========
Verify(module_id) ==
    LET module == modules[module_id]
    IN /\ module.state = PARSING
       /\ module.parsed_bytes > 0
       /\ modules' = [modules EXCEPT ![module_id].state = VERIFYING]
       /\ UNCHANGED <<allocator, alloc_ptr, import_table, error_state, load_order>>

-- ========== Phase 3: Ready（準備完了、依存関係解決）==========
Ready(module_id) ==
    LET module == modules[module_id]
    IN /\ module.state = VERIFYING
       /\ modules' = [modules EXCEPT ![module_id].state = READY]
       /\ UNCHANGED <<allocator, alloc_ptr, import_table, error_state, load_order>>

-- ========== Phase 2c: Rollback（パース失敗時の巻き戻し）==========
-- パース途中または検証失敗時にメモリを回収
Rollback(module_id) ==
    LET module == modules[module_id]
        is_parsing_or_verifying == (module.state \in {PARSING, VERIFYING})
        last_module_in_order == IF Len(load_order) > 0 THEN load_order[Len(load_order)] ELSE -1
    IN /\ is_parsing_or_verifying
       /\ last_module_in_order = module_id  -- LIFO: 最後にロードしたモジュールのみRollback可能
       /\ LET rollback_size == module.parsed_bytes
          IN /\ alloc_ptr' = alloc_ptr - rollback_size
             /\ allocator' = [allocator EXCEPT ![alloc_ptr - rollback_size] =
                    [owner_module |-> -1, size |-> 0, timestamp |-> 0]]
             /\ load_order' = SubSeq(load_order, 1, Len(load_order) - 1)
       /\ modules' = [modules EXCEPT ![module_id].state = IDLE,
                      ![module_id].parsed_bytes = 0]
       /\ error_state' = [module_id |-> module_id, parse_offset |-> module.parsed_bytes,
                          reason |-> "parse_fail"]
       /\ UNCHANGED <<import_table>>

-- ========== Phase 4: Unload（アンロード、LIFO確認）==========
Unload(module_id) ==
    LET module == modules[module_id]
        last_module_in_order == IF Len(load_order) > 0 THEN load_order[Len(load_order)] ELSE -1
    IN /\ module.state = READY
       /\ last_module_in_order = module_id  -- LIFO: 最後にロードしたモジュールのみUnload可能
       /\ LET rollback_size == module.parsed_bytes
          IN /\ alloc_ptr' = alloc_ptr - rollback_size
             /\ allocator' = [allocator EXCEPT ![alloc_ptr - rollback_size] =
                    [owner_module |-> -1, size |-> 0, timestamp |-> 0]]
             /\ load_order' = SubSeq(load_order, 1, Len(load_order) - 1)
       /\ modules' = [modules EXCEPT ![module_id].state = IDLE,
                      ![module_id].parsed_bytes = 0]
       /\ UNCHANGED <<import_table, error_state>>

-- ========== Transition ==========
Next ==
    \/ \E m \in 0..MAX_MODULES-1:
        \E size \in 1..100:
            Prepare(m, size)
    \/ \E m \in 0..MAX_MODULES-1:
        \E size \in 1..100:
            Parse(m, size)
    \/ \E m \in 0..MAX_MODULES-1:
        Verify(m)
    \/ \E m \in 0..MAX_MODULES-1:
        Ready(m)
    \/ \E m \in 0..MAX_MODULES-1:
        Rollback(m)
    \/ \E m \in 0..MAX_MODULES-1:
        Unload(m)

-- ========== 不変条件 ==========

-- 不変条件1: アロケータポインタの単調性 {BumpAllocator}
AllocatorMonotonicity ==
    /\ alloc_ptr >= 0
    /\ alloc_ptr <= ALLOCATOR_SIZE

-- 不変条件2: LIFO メモリ制約 {BumpAllocator}
LIFOMemoryConstraint ==
    \A m \in 0..MAX_MODULES-1:
        (modules[m].parsed_bytes > 0) =>
            (\E i \in 0..alloc_ptr-1:
                allocator[i].owner_module = m /\
                allocator[i].size = modules[m].parsed_bytes)

-- 不変条件3: LIFO アンロード順序 {BumpAllocator}
LIFOUnloadOrder ==
    \A m1 \in 0..MAX_MODULES-1:
    \A m2 \in 0..MAX_MODULES-1:
        (m1 # m2 /\
         modules[m1].state = IDLE /\
         modules[m2].state # IDLE) =>
            (LET idx1 == CHOOSE i \in 1..Len(load_order): load_order[i] = m1
                 idx2 == CHOOSE i \in 1..Len(load_order): load_order[i] = m2
             IN idx1 > idx2)

-- 不変条件4: メモリリーク防止 {BumpAllocator}
NoMemoryLeak ==
    (\A m \in 0..MAX_MODULES-1:
        modules[m].state = IDLE =>
            (modules[m].parsed_bytes = 0))
    /\
    (\A m \in 0..MAX_MODULES-1:
        modules[m].state # IDLE =>
            (modules[m].parsed_bytes > 0))

-- 不変条件5: パース状態の一貫性 {ROMParsing}
ParseConsistency ==
    \A m \in 0..MAX_MODULES-1:
        (modules[m].state = IDLE) =>
            (modules[m].parsed_bytes = 0)

-- 不変条件6: ロードオーダー一意性
LoadOrderUniqueness ==
    Len(load_order) = Cardinality({m \in 0..MAX_MODULES-1: modules[m].state # IDLE})

-- 不変条件7: Rollback 後の状態 {BumpAllocator}
RollbackStateConsistency ==
    (error_state.module_id >= 0) =>
        (modules[error_state.module_id].state = IDLE)

Invariants ==
    /\ AllocatorMonotonicity
    /\ LIFOMemoryConstraint
    /\ LIFOUnloadOrder
    /\ NoMemoryLeak
    /\ ParseConsistency
    /\ LoadOrderUniqueness
    /\ RollbackStateConsistency

Spec == Init /\ [][Next]_vars

-- ========== LTL 特性 ==========

-- Liveness: ロードしたモジュールは最終的にアンロード可能
EventuallyUnloadable ==
    \A m \in 0..MAX_MODULES-1:
        (modules[m].state = READY) ~> (modules[m].state = IDLE)

================================================================================
