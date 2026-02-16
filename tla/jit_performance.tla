-------------------------- MODULE jit_performance --------------------------
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    Blocks,
    TraceLength,
    CacheSize,
    CompileCost,
    JitExecCost,
    InterpExecCost,
    HotspotThreshold

VARIABLES
    trace_index,
    cache,
    counters,
    jit_cost,
    interp_cost,
    execution_trace

vars == <<trace_index, cache, counters, jit_cost, interp_cost, execution_trace>>

TypeInvariant ==
    /\ trace_index \in 1..(TraceLength + 1)
    /\ cache \subseteq Blocks
    /\ Cardinality(cache) <= CacheSize
    /\ counters \in [Blocks -> Nat]
    /\ jit_cost \in Nat
    /\ interp_cost \in Nat
    /\ execution_trace \in Seq(Blocks)

Init ==
    /\ trace_index = 1
    /\ cache = {}
    /\ counters = [b \in Blocks |-> 0]
    /\ jit_cost = 0
    /\ interp_cost = 0
    /\ execution_trace = <<>> 

AdvanceInterpreter ==
    interp_cost' = interp_cost + InterpExecCost

AdvanceJIT(current_block) ==
    IF current_block \in cache THEN
        /\ jit_cost' = jit_cost + JitExecCost
        /\ UNCHANGED <<cache, counters>>
    ELSE
        IF counters[current_block] + 1 >= HotspotThreshold THEN
            /\ IF Cardinality(cache) < CacheSize THEN
                    cache' = cache \cup {current_block}
                ELSE
                    \E evict \in cache : cache' = (cache \ {evict}) \cup {current_block}
            /\ jit_cost' = jit_cost + CompileCost + JitExecCost
            /\ counters' = counters
        ELSE
            /\ jit_cost' = jit_cost + InterpExecCost
            /\ counters' = [counters EXCEPT ![current_block] = @ + 1]
            /\ UNCHANGED <<cache>>

Next ==
    /\ trace_index <= TraceLength
    /\ \E next_block \in Blocks :
        /\ execution_trace' = Append(execution_trace, next_block)
        /\ trace_index' = trace_index + 1
        /\ AdvanceInterpreter
        /\ AdvanceJIT(next_block)

Done ==
    /\ trace_index > TraceLength
    /\ UNCHANGED vars

Spec == Init /\ [][Next \/ Done]_vars

JITIsFaster == 
    (trace_index > TraceLength) => (jit_cost < interp_cost)

=============================================================================
