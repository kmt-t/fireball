"""
examples/state_machine_demo.py: StateMachine DSL による宣言的モデル定義・検証・TLA+ 出力デモ
"""

from tools.verifier import StateMachine, ModelChecker, TLAGenerator, generate_markdown_report

def create_mutex_state_machine() -> StateMachine:
    """宣言的 StateMachine DSL による Mutex モデル"""
    sm = StateMachine("DeclarativeMutex", allow_deadlock=False)
    
    # 1. 変数と初期値
    sm.variable("p1_state", "idle")
    sm.variable("p2_state", "idle")
    sm.variable("mutex_owner", None)

    # 2. 状態遷移 (src -> event -> dst)
    # Process 1
    sm.transition("P1_Request", src={"p1_state": "idle"}, dst={"p1_state": "waiting"})
    sm.transition("P1_Acquire", src={"p1_state": "waiting", "mutex_owner": None}, dst={"p1_state": "critical", "mutex_owner": "P1"})
    sm.transition("P1_Release", src={"p1_state": "critical"}, dst={"p1_state": "idle", "mutex_owner": None})

    # Process 2
    sm.transition("P2_Request", src={"p2_state": "idle"}, dst={"p2_state": "waiting"})
    sm.transition("P2_Acquire", src={"p2_state": "waiting", "mutex_owner": None}, dst={"p2_state": "critical", "mutex_owner": "P2"})
    sm.transition("P2_Release", src={"p2_state": "critical"}, dst={"p2_state": "idle", "mutex_owner": None})

    # 3. 不変式
    sm.invariant(
        "MutualExclusion",
        predicate=lambda s: not (s['p1_state'] == 'critical' and s['p2_state'] == 'critical'),
        tla_expr="~ (p1_state = \"critical\" /\\ p2_state = \"critical\")",
        description="両プロセスが同時に Critical Section に入ることは禁止"
    )

    return sm

def main():
    sm = create_mutex_state_machine()
    model = sm.to_model()

    print("========================================")
    print(" 1. 宣言的 StateMachine からの TLA+ 出力")
    print("========================================")
    gen = TLAGenerator(model)
    print(gen.generate_tla())

    print("========================================")
    print(" 2. モデルチェッカーによる検証結果")
    print("========================================")
    checker = ModelChecker(model)
    result = checker.verify()
    print(generate_markdown_report(result, model.name))

if __name__ == "__main__":
    main()
