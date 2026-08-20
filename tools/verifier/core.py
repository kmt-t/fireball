"""
core.py: 汎用形式検証フレームワークの基本構造体および型定義
"""

from typing import Dict, Any, Callable, List, Optional, Tuple, Set, Union
from dataclasses import dataclass, field
from enum import Enum, auto
import json

class ResultStatus(Enum):
    PASSED = auto()
    INVARIANT_VIOLATED = auto()
    DEADLOCK_DETECTED = auto()
    MAX_STATES_EXCEEDED = auto()

@dataclass(frozen=True)
class State:
    """システムの状態を表現するイミュータブルなハッシュ可能クラス"""
    data: Tuple[Tuple[str, Any], ...]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'State':
        """辞書オブジェクトから State を作成（ネスト構造も再帰的にソート tuple 化）"""
        def make_hashable(val: Any) -> Any:
            if isinstance(val, dict):
                return tuple(sorted((k, make_hashable(v)) for k, v in val.items()))
            elif isinstance(val, (list, set)):
                return tuple(make_hashable(x) for x in val)
            return val

        sorted_items = tuple(sorted((k, make_hashable(v)) for k, v in d.items()))
        return cls(data=sorted_items)

    def to_dict(self) -> Dict[str, Any]:
        """State を通常の辞書オブジェクトに戻す"""
        def restore_val(val: Any) -> Any:
            if isinstance(val, tuple):
                # tuple of (key, val) pairs or sequence
                if all(isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], str) for x in val):
                    return {k: restore_val(v) for k, v in val}
                return [restore_val(x) for x in val]
            return val

        return {k: restore_val(v) for k, v in self.data}

    def get(self, key: str, default: Any = None) -> Any:
        d = self.to_dict()
        return d.get(key, default)

    def __getitem__(self, key: str) -> Any:
        d = self.to_dict()
        return d[key]

    def set(self, key: str, value: Any) -> 'State':
        """指定したキーの値を変更した新しい State を返す"""
        d = self.to_dict()
        d[key] = value
        return State.from_dict(d)

    def update(self, **kwargs) -> 'State':
        """複数キーの値を更新した新しい State を返す"""
        d = self.to_dict()
        d.update(kwargs)
        return State.from_dict(d)

    def __repr__(self) -> str:
        d = self.to_dict()
        return f"State({json.dumps(d, ensure_ascii=False)})"

@dataclass
class Rule:
    """状態遷移ルール"""
    name: str
    guard: Callable[[State], bool]
    effect: Callable[[State], State]
    description: str = ""

    def is_enabled(self, state: State) -> bool:
        try:
            return self.guard(state)
        except Exception:
            return False

    def apply(self, state: State) -> State:
        return self.effect(state)

@dataclass
class Invariant:
    """検証対象の不変式"""
    name: str
    predicate: Callable[[State], bool]
    description: str = ""

    def holds(self, state: State) -> bool:
        try:
            return self.predicate(state)
        except Exception:
            return False

@dataclass
class CounterexampleStep:
    """反例の1ステップ（どの状態からどのアクションを実行して次の状態に至ったか）"""
    step: int
    action: Optional[str]
    state: State

@dataclass
class VerificationResult:
    """モデル検証結果のサマリ"""
    status: ResultStatus
    states_explored: int
    transitions_explored: int
    violated_invariant: Optional[Invariant] = None
    deadlock_state: Optional[State] = None
    counterexample: List[CounterexampleStep] = field(default_factory=list)
    execution_time_sec: float = 0.0
    reachable_states: Set[State] = field(default_factory=set)
    state_graph: Dict[State, List[Tuple[str, State]]] = field(default_factory=dict)
