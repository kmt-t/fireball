-----------------------------------------------------------------------------
-- Interpreter.tla
-- Fireball WASM Interpreter Execution Model Verification
-- Focus: Interpreter/JIT mode switching and Preemption (Yield)
-----------------------------------------------------------------------------
---- MODULE Interpreter ----
EXTENDS Naturals, FiniteSets

CONSTANTS InterpreterMode, JITMode, FuelLimit

VARIABLES mode, pc, fuel, interrupted

vars == <<mode, pc, fuel, interrupted>>

Init ==
    /\ mode = InterpreterMode
    /\ pc \in 0..100
    /\ fuel = 0
    /\ interrupted = FALSE

NextStep ==
    /\ IF mode = InterpreterMode THEN
        /\ IF fuel < FuelLimit THEN
            /\ fuel' = fuel + 1
            /\ pc' = pc + 1
            /\ mode' = mode
            /\ UNCHANGED interrupted
        ELSE
            /\ mode' = JITMode
            /\ fuel' = 0
            /\ UNCHANGED <<pc, interrupted>>
       ELSE
        /\ IF interrupted THEN
            /\ mode' = InterpreterMode
            /\ fuel' = 0
            /\ interrupted' = FALSE
            /\ UNCHANGED pc
        ELSE
            /\ mode' = mode
            /\ fuel' = fuel + 1
            /\ UNCHANGED <<pc, interrupted>>

Spec == Init /\ [][NextStep]_vars

---
-- Invariants
-- 1.モード遷移の妥当性 (Interpreter -> JIT は fuel 閾値越えのみ)
-- 2.安全性の確認 (Interrupted なら必ず InterpreterMode に戻る)
-----------------------------------------------------------------------------
TypeOK ==
    /\ mode \in {InterpreterMode, JITMode}
    /\ fuel \in 0..FuelLimit
    /\ pc \in 0..200

Safety ==
    (mode = JITMode /\ interrupted) => (mode' = InterpreterMode)

=============================================================================
