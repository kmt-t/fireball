
---- MODULE LoaderRollbackVerification ----
EXTENDS Integers, Sequences, FiniteSets


CONSTANT MAX_MODULES, MAX_FUNCTIONS_PER_MODULE, ALLOCATOR_SIZE
CONSTANT MAX_IMPORTS
ASSUME MAX_MODULES = 4
ASSUME MAX_FUNCTIONS_PER_MODULE = 256
ASSUME ALLOCATOR_SIZE = 1024
ASSUME MAX_IMPORTS = 32


CONSTANT IDLE, PARSING, VERIFYING, READY, ERROR


CONSTANT HEADER_PARSED, SECTIONS_SCANNED, EXPORTS_INDEXED, IMPORTS_RESOLVED


VARIABLES
    modules,            
    allocator,          
    alloc_ptr,          
    import_table,       
    error_state,        
    load_order          

vars == <<modules, allocator, alloc_ptr, import_table, error_state, load_order>>


Init ==
    /\ modules = [m \in 0..MAX_MODULES-1 |-> [state |-> IDLE, parsed_bytes |-> 0,
                                                exports |-> <<>>, imports |-> <<>>]]
    /\ allocator = [i \in 0..ALLOCATOR_SIZE-1 |-> [owner_module |-> -1, size |-> 0, timestamp |-> 0]]
    /\ alloc_ptr = 0
    /\ import_table = [m \in 0..MAX_MODULES-1 |-> [i \in 0..MAX_IMPORTS-1 |->
                       [module_name |-> "", export_name |-> "", resolved |-> FALSE]]]
    /\ error_state = [module_id |-> -1, parse_offset |-> 0, reason |-> ""]
    /\ load_order = <<>>


Prepare(module_id, binary_size) ==
    LET module == modules[module_id]
    IN /\ module.state = IDLE
       /\ binary_size > 0
       /\ binary_size <= ALLOCATOR_SIZE - alloc_ptr  
       /\ modules' = [modules EXCEPT ![module_id].state = PARSING]
       /\ UNCHANGED <<allocator, alloc_ptr, import_table, error_state, load_order>>


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


Verify(module_id) ==
    LET module == modules[module_id]
    IN /\ module.state = PARSING
       /\ module.parsed_bytes > 0
       /\ modules' = [modules EXCEPT ![module_id].state = VERIFYING]
       /\ UNCHANGED <<allocator, alloc_ptr, import_table, error_state, load_order>>


Ready(module_id) ==
    LET module == modules[module_id]
    IN /\ module.state = VERIFYING
       /\ modules' = [modules EXCEPT ![module_id].state = READY]
       /\ UNCHANGED <<allocator, alloc_ptr, import_table, error_state, load_order>>



Rollback(module_id) ==
    LET module == modules[module_id]
        is_parsing_or_verifying == (module.state \in {PARSING, VERIFYING})
        last_module_in_order == IF Len(load_order) > 0 THEN load_order[Len(load_order)] ELSE -1
    IN /\ is_parsing_or_verifying
       /\ last_module_in_order = module_id  
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


Unload(module_id) ==
    LET module == modules[module_id]
        last_module_in_order == IF Len(load_order) > 0 THEN load_order[Len(load_order)] ELSE -1
    IN /\ module.state = READY
       /\ last_module_in_order = module_id  
       /\ LET rollback_size == module.parsed_bytes
          IN /\ alloc_ptr' = alloc_ptr - rollback_size
             /\ allocator' = [allocator EXCEPT ![alloc_ptr - rollback_size] =
                    [owner_module |-> -1, size |-> 0, timestamp |-> 0]]
             /\ load_order' = SubSeq(load_order, 1, Len(load_order) - 1)
       /\ modules' = [modules EXCEPT ![module_id].state = IDLE,
                      ![module_id].parsed_bytes = 0]
       /\ UNCHANGED <<import_table, error_state>>


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




AllocatorMonotonicity ==
    /\ alloc_ptr >= 0
    /\ alloc_ptr <= ALLOCATOR_SIZE


LIFOMemoryConstraint ==
    \A m \in 0..MAX_MODULES-1:
        (modules[m].parsed_bytes > 0) =>
            (\E i \in 0..alloc_ptr-1:
                allocator[i].owner_module = m /\
                allocator[i].size = modules[m].parsed_bytes)


LIFOUnloadOrder ==
    \A m1 \in 0..MAX_MODULES-1:
    \A m2 \in 0..MAX_MODULES-1:
        (m1 # m2 /\
         modules[m1].state = IDLE /\
         modules[m2].state # IDLE) =>
            (LET idx1 == CHOOSE i \in 1..Len(load_order): load_order[i] = m1
                 idx2 == CHOOSE i \in 1..Len(load_order): load_order[i] = m2
             IN idx1 > idx2)


NoMemoryLeak ==
    (\A m \in 0..MAX_MODULES-1:
        modules[m].state = IDLE =>
            (modules[m].parsed_bytes = 0))
    /\
    (\A m \in 0..MAX_MODULES-1:
        modules[m].state # IDLE =>
            (modules[m].parsed_bytes > 0))


ParseConsistency ==
    \A m \in 0..MAX_MODULES-1:
        (modules[m].state = IDLE) =>
            (modules[m].parsed_bytes = 0)


LoadOrderUniqueness ==
    Len(load_order) = Cardinality({m \in 0..MAX_MODULES-1: modules[m].state # IDLE})


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




EventuallyUnloadable ==
    \A m \in 0..MAX_MODULES-1:
        (modules[m].state = READY) ~> (modules[m].state = IDLE)

====
