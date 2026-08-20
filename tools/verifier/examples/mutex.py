"""
examples/mutex.py: 2プロセス間におけるミューテックス相互排除・デッドロックモデルの検証
"""

from tools.verifier import (
    State, ModelChecker, state_model, rule, invariant,
    generate_markdown_report
)

@state_model(name="TwoProcessMutex", allow_deadlock=False)
class TwoProcessMutexModel:
    """
    2つのプロセス (P1, P2) が共有リソース (Mutex) を獲得・解放する状態遷移モデル
    状態:
      - p1_state: 'idle', 'waiting', 'critical'
      - p2_state: 'idle', 'waiting', 'critical'
      - mutex_owner: None, 'P1', 'P2'
    """
    def init_state(self) -> dict:
        return {
            'p1_state': 'idle',
            'p2_state': 'idle',
            'mutex_owner': None
        }

    # --- Process 1 Rules ---
    @rule(name="P1_Request", when=lambda s: s['p1_state'] == 'idle')
    def p1_request(self, s: State) -> State:
        return s.set('p1_state', 'waiting')

    @rule(name="P1_Acquire", when=lambda s: s['p1_state'] == 'waiting' and s['mutex_owner'] is None)
    def p1_acquire(self, s: State) -> State:
        return s.update(p1_state='critical', mutex_owner='P1')

    @rule(name="P1_Release", when=lambda s: s['p1_state'] == 'critical')
    def p1_release(self, s: State) -> State:
        return s.update(p1_state='idle', mutex_owner=None)

    # --- Process 2 Rules ---
    @rule(name="P2_Request", when=lambda s: s['p2_state'] == 'idle')
    def p2_request(self, s: State) -> State:
        return s.set('p2_state', 'waiting')

    @rule(name="P2_Acquire", when=lambda s: s['p2_state'] == 'waiting' and s['mutex_owner'] is None)
    def p2_acquire(self, s: State) -> State:
        return s.update(p2_state='critical', mutex_owner='P2')

    @rule(name="P2_Release", when=lambda s: s['p2_state'] == 'critical')
    def p2_release(self, s: State) -> State:
        return s.update(p2_state='idle', mutex_owner=None)

    # --- Invariants ---
    @invariant(name="MutualExclusion", description="両プロセスが同時に Critical Section に入ることは禁止")
    def inv_mutual_exclusion(self, s: State) -> bool:
        return not (s['p1_state'] == 'critical' and s['p2_state'] == 'critical')

    @invariant(name="ValidMutexOwner", description="Mutex の所有者は状態と一貫していなければならない")
    def inv_valid_owner(self, s: State) -> bool:
        if s['mutex_owner'] == 'P1':
            return s['p1_state'] == 'critical'
        elif s['mutex_owner'] == 'P2':
            return s['p2_state'] == 'critical'
        else:
            return s['p1_state'] != 'critical' and s['p2_state'] != 'critical'

def run():
    checker = ModelChecker(TwoProcessMutexModel.model)
    result = checker.verify()
    report = generate_markdown_report(result, "TwoProcessMutex")
    print(report)
    return result

if __name__ == "__main__":
    run()
