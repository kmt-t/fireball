"""
state_machine.py: 宣言的ステートマシン (State Machine) DSL 定義
"""

from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from .core import State, Rule, Invariant
from .checker import Model

@dataclass
class Transition:
    """ステートマシンの 1 つの状態遷移 (src -> event -> dst)"""
    event: str
    src: Dict[str, Any]
    dst: Dict[str, Any]
    guard: Optional[Callable[[State], bool]] = None
    description: str = ""

    def matches_src(self, state: State) -> bool:
        st_dict = state.to_dict()
        for k, v in self.src.items():
            if st_dict.get(k) != v:
                return False
        if self.guard and not self.guard(state):
            return False
        return True

    def apply(self, state: State) -> State:
        st_dict = state.to_dict()
        st_dict.update(self.dst)
        return State.from_dict(st_dict)

class StateMachine:
    """宣言的ステートマシン DSL クラス"""
    def __init__(self, name: str = "StateMachine", allow_deadlock: bool = False):
        self.name = name
        self.variables: Dict[str, Any] = {}
        self.transitions_list: List[Transition] = []
        self.invariants_list: List[Invariant] = []
        self.allow_deadlock: bool = allow_deadlock

    def variable(self, name: str, initial: Any) -> 'StateMachine':
        """状態変数の宣言と初期値の設定"""
        self.variables[name] = initial
        return self

    def transition(
        self,
        event: str,
        src: Dict[str, Any],
        dst: Dict[str, Any],
        guard: Optional[Callable[[State], bool]] = None,
        description: str = ""
    ) -> 'StateMachine':
        """状態遷移 (src -> event -> dst) の宣言"""
        t = Transition(event=event, src=src, dst=dst, guard=guard, description=description)
        self.transitions_list.append(t)
        return self

    def invariant(
        self,
        name: str,
        predicate: Callable[[State], bool],
        tla_expr: Optional[str] = None,
        description: str = ""
    ) -> 'StateMachine':
        """不変式の宣言"""
        inv = Invariant(name=name, predicate=predicate, description=description)
        if tla_expr:
            inv.tla_predicate = tla_expr
        self.invariants_list.append(inv)
        return self

    def to_model(self) -> Model:
        """StateMachine を検証用 Model に変換"""
        model = Model(name=self.name)
        model.allow_deadlock = self.allow_deadlock

        # 初期状態
        init_st = State.from_dict(self.variables)
        model.add_initial_state(init_st)

        # 遷移を Rule に変換
        for t in self.transitions_list:
            rule_obj = Rule(
                name=t.event,
                guard=t.matches_src,
                effect=t.apply,
                description=t.description
            )
            # TLA+ 生成用のメタ情報を保存
            rule_obj.transition_info = t
            model.add_rule(rule_obj)

        # 不変式の登録
        for inv in self.invariants_list:
            model.add_invariant(inv)

        return model
