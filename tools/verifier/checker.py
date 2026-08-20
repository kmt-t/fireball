"""
checker.py: TLA+ TLC バックエンドによるモデル検証エンジン
"""

from typing import List, Dict, Set, Optional, Tuple, Any
from .core import State, Rule, Invariant, ResultStatus, VerificationResult, CounterexampleStep

class Model:
    """検証モデル定義クラス"""
    def __init__(self, name: str = "Model"):
        self.name = name
        self.initial_states: List[State] = []
        self.rules: List[Rule] = []
        self.invariants: List[Invariant] = []
        self.allow_deadlock: bool = False

    def add_initial_state(self, state: State) -> None:
        self.initial_states.append(state)

    def add_rule(self, rule: Rule) -> None:
        self.rules.append(rule)

    def add_invariant(self, invariant: Invariant) -> None:
        self.invariants.append(invariant)

class ModelChecker:
    """TLA+ TLC バックエンド専用モデル検証エンジン"""
    def __init__(self, model: Model, max_states: int = 100000, backend: str = "tlc"):
        self.model = model
        self.max_states = max_states
        self.backend = "tlc"

    def verify(self, work_dir: Optional[Any] = None) -> VerificationResult:
        """TLA+ トランスパイルおよび TLC バックエンドによる検証の実行"""
        return self.verify_tla(work_dir=work_dir)

    def verify_tla(self, work_dir: Optional[Any] = None) -> VerificationResult:
        """TLA+ トランスパイルおよび TLC バックエンドによる検証の実行"""
        from .tlc_runner import TLCRunner
        runner = TLCRunner(self.model, work_dir=work_dir)
        result, _, _ = runner.run()
        return result
