---------------------------- MODULE counterexample ----------------------------

EXTENDS COOS_EventDriven

(* Constant initialization state *)
ConstInit ==
  MaxQueueSize = 3
    /\ Messages = {"ModelValue_m1"}
    /\ Tasks = { "ModelValue_t1", "ModelValue_t2" }

(* Initial state [_transition(0)] *)
State0 ==
  MaxQueueSize = 3
    /\ Messages = {"ModelValue_m1"}
    /\ Tasks = { "ModelValue_t1", "ModelValue_t2" }
    /\ eventQueue = <<>>
    /\ ownership = SetAsFun({<<"ModelValue_m1", "t1">>})
    /\ taskState
      = SetAsFun({ <<"ModelValue_t1", "IDLE">>, <<"ModelValue_t2", "IDLE">> })
    /\ waitingFor
      = SetAsFun({ <<"ModelValue_t1", "NONE">>, <<"ModelValue_t2", "NONE">> })

(* The following formula holds true in the last state and violates the invariant *)
InvariantViolation ==
  Skolem((\E m_5 \in Messages:
    ~(ownership[m_5] = "NONE")
      /\ ((\A t_7 \in Tasks: ~(ownership[m_5] = t_7))
        /\ ~(ownership[m_5] = "IN_FLIGHT"))))

================================================================================
(* Created by Apalache on Fri May 01 07:55:35 JST 2026 *)
(* https://github.com/apalache-mc/apalache *)
