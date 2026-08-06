"""
formal_verifier: 汎用明示的状態モデルチェッカー (Explicit-State Model Checker) フレームワーク
"""

from .core import State, Rule, Invariant, ResultStatus, VerificationResult, CounterexampleStep
from .checker import Model, ModelChecker
from .dsl import state_model, rule, invariant
from .state_machine import StateMachine, Transition
from .tla_generator import TLAGenerator
from .tlc_runner import TLCRunner
from .reporter import generate_markdown_report, generate_mermaid_diagram

__all__ = [
    "State",
    "Model",
    "Rule",
    "Invariant",
    "ResultStatus",
    "VerificationResult",
    "CounterexampleStep",
    "ModelChecker",
    "StateMachine",
    "Transition",
    "TLAGenerator",
    "TLCRunner",
    "state_model",
    "rule",
    "invariant",
    "generate_markdown_report",
    "generate_mermaid_diagram",
]
