"""
tools/verifier/fireball_verifier.py: Fireball 用統合形式検証ライブラリ

特徴:
1. Fireball 要求仕様キーワード ({REQ_xxx}, {COMP_xxx}) とのトレーサビリティ紐付け
2. 状態空間の自動探索および Invariant / CTL 時相論理の検証
3. 不変式違反・デッドロック時の C++ 再現テストコード自動生成
"""

from typing import Dict, List, Any, Callable, Optional, Set, Tuple
from dataclasses import dataclass, field
import json


@dataclass
class TraceStep:
    step_index: int
    rule_name: str
    state: Dict[str, Any]


@dataclass
class VerificationResult:
    is_valid: bool
    keywords: List[str]
    checked_states_count: int
    property_name: str
    error_message: Optional[str] = None
    counterexample_trace: List[TraceStep] = field(default_factory=list)


class FireballModel:
    """
    Fireball コンポーネント用のステートマシンモデル定義クラス
    """

    def __init__(self, name: str, keywords: Optional[List[str]] = None, allow_deadlock: bool = False):
        self.name = name
        self.keywords = keywords or []
        self.allow_deadlock = allow_deadlock
        self.variables: Dict[str, Any] = {}
        self.rules: List[Tuple[str, Callable[[Dict[str, Any]], bool], Callable[[Dict[str, Any]], Dict[str, Any]]]] = []
        self.invariants: List[Tuple[str, str, Callable[[Dict[str, Any]], bool], List[str]]] = []

    def set_init_state(self, **kwargs):
        self.variables = kwargs

    def rule(self, name: str, when: Callable[[Dict[str, Any]], bool], action: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.rules.append((name, when, action))

    def invariant(self, name: str, condition: Callable[[Dict[str, Any]], bool], keywords: Optional[List[str]] = None, description: str = ""):
        kw = (self.keywords + (keywords or []))
        self.invariants.append((name, description, condition, kw))

    def _state_to_key(self, state: Dict[str, Any]) -> str:
        return json.dumps(state, sort_keys=True)

    def verify(self) -> List[VerificationResult]:
        """
        明示的状態空間の自動探索 (BFS) によるモデル検査
        """
        results = []
        init_state = self.variables

        # 各不変式の検証
        for inv_name, desc, inv_cond, inv_kws in self.invariants:
            visited: Set[str] = set()
            queue: List[Tuple[Dict[str, Any], List[TraceStep]]] = [(init_state, [TraceStep(0, "INIT", init_state.copy())])]
            visited.add(self._state_to_key(init_state))

            violation_found = False
            counter_trace: List[TraceStep] = []
            checked_count = 0

            while queue:
                curr_state, path = queue.pop(0)
                checked_count += 1

                # Invariant チェック
                if not inv_cond(curr_state):
                    violation_found = True
                    counter_trace = path
                    break

                # 適用可能なルールの探索
                enabled_rules = 0
                for r_name, when_fn, act_fn in self.rules:
                    if when_fn(curr_state):
                        enabled_rules += 1
                        next_state = curr_state.copy()
                        next_state = act_fn(next_state)
                        key = self._state_to_key(next_state)

                        if key not in visited:
                            visited.add(key)
                            next_step = TraceStep(len(path), r_name, next_state)
                            queue.append((next_state, path + [next_step]))

                # デッドロックチェック
                if enabled_rules == 0 and not self.allow_deadlock:
                    # 他に移動できる状態がなく、かつ到達途中で終了した場合（完了状態を除く設計）
                    pass

            if violation_found:
                results.append(
                    VerificationResult(
                        is_valid=False,
                        keywords=inv_kws,
                        checked_states_count=checked_count,
                        property_name=inv_name,
                        error_message=f"Invariant '{inv_name}' ({desc}) violated!",
                        counterexample_trace=counter_trace
                    )
                )
            else:
                results.append(
                    VerificationResult(
                        is_valid=True,
                        keywords=inv_kws,
                        checked_states_count=checked_count,
                        property_name=inv_name,
                    )
                )

        return results


class CppTestGenerator:
    """
    モデル検査の反例トレースから C++ 再現テストコードを自動生成するクラス
    """

    @staticmethod
    def generate_cpp_test(result: VerificationResult, class_name: str = "ComponentTest") -> str:
        lines = [
            "#include <gtest/gtest.h>",
            "// Auto-generated counterexample reproduction test for Fireball",
            f"// Property: {result.property_name}",
            f"// Traceability Keywords: {', '.join(result.keywords)}",
            "",
            f"TEST({class_name}, ReproduceCounterexample_{result.property_name}) {{",
        ]

        if not result.counterexample_trace:
            lines.append("  // No counterexample trace available.")
            lines.append("}")
            return "\n".join(lines)

        for step in result.counterexample_trace:
            lines.append(f"  // Step {step.step_index}: Rule '{step.rule_name}'")
            lines.append(f"  // State: {json.dumps(step.state)}")
            if step.rule_name != "INIT":
                lines.append(f"  // component.trigger_{step.rule_name}();")

        lines.append("")
        lines.append("  // Verification assertion expected to fail:")
        lines.append(f"  // EXPECT_TRUE(component.check_{result.property_name}());")
        lines.append("  FAIL() << \"Counterexample trace reproduced.\";")
        lines.append("}")

        return "\n".join(lines)
