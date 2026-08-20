"""
dsl.py: 直観的なモデル定義のための DSL / デコレータ API
"""

from typing import Callable, Any, Optional, List
from functools import wraps
from .core import State, Rule, Invariant
from .checker import Model

class RuleBuilder:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.guard_func: Optional[Callable[[State], bool]] = None
        self.effect_func: Optional[Callable[[State], State]] = None

    def guard(self, func: Callable[[State], bool]) -> 'RuleBuilder':
        self.guard_func = func
        return self

    def effect(self, func: Callable[[State], State]) -> 'RuleBuilder':
        self.effect_func = func
        return self

    def build(self) -> Rule:
        g = self.guard_func if self.guard_func else (lambda s: True)
        e = self.effect_func if self.effect_func else (lambda s: s)
        return Rule(name=self.name, guard=g, effect=e, description=self.description)

def rule(name: Optional[str] = None, description: str = "", when: Optional[Callable[[State], bool]] = None):
    """
    ルール定義用デコレータ
    使用法 1:
        @rule(name="Inc", when=lambda s: s['count'] < 5)
        def increment(state: State) -> State:
            return state.set('count', state['count'] + 1)

    使用法 2:
        @rule()
        def increment(state: State) -> State:
            ...
    """
    def decorator(func: Callable[[State], State]):
        r_name = name or func.__name__
        g_func = when if when is not None else (lambda s: True)
        
        # 属性として Rule オブジェクトを紐付け
        func._is_rule = True
        func._rule_obj = Rule(name=r_name, guard=g_func, effect=func, description=description)
        return func
    return decorator

def invariant(name: Optional[str] = None, description: str = ""):
    """
    不変式定義用デコレータ
    使用法:
        @invariant(name="CountBoundary")
        def check_bound(state: State) -> bool:
            return state['count'] <= 10
    """
    def decorator(func: Callable[[State], bool]):
        inv_name = name or func.__name__
        func._is_invariant = True
        func._invariant_obj = Invariant(name=inv_name, predicate=func, description=description)
        return func
    return decorator

def state_model(name: Optional[str] = None, allow_deadlock: bool = False):
    """
    クラスデコレータ。クラス内の初期状態、ルール、不変式を集約して Model を構築する。
    """
    def decorator(cls: type):
        m_name = name or cls.__name__
        model_obj = Model(name=m_name)
        model_obj.allow_deadlock = allow_deadlock

        # インスタンス化
        instance = cls()

        # 1. 初期状態の登録 (init_state メソッドまたは initial_states 属性)
        if hasattr(instance, 'initial_states') and isinstance(instance.initial_states, list):
            for st in instance.initial_states:
                if isinstance(st, dict):
                    model_obj.add_initial_state(State.from_dict(st))
                elif isinstance(st, State):
                    model_obj.add_initial_state(st)
        elif hasattr(instance, 'init_state') and callable(getattr(instance, 'init_state')):
            st = instance.init_state()
            if isinstance(st, dict):
                model_obj.add_initial_state(State.from_dict(st))
            elif isinstance(st, State):
                model_obj.add_initial_state(st)

        # 2. クラスメソッド/属性をスキャンして Rule と Invariant を収集
        for attr_name in dir(instance):
            attr = getattr(instance, attr_name)
            if hasattr(attr, '_is_rule') and getattr(attr, '_is_rule'):
                base_rule = getattr(attr, '_rule_obj')
                # attr は bound method なので attr(state) で呼び出せるようにラップ
                bound_effect = attr
                bound_guard = base_rule.guard
                rule_obj = Rule(
                    name=base_rule.name,
                    guard=bound_guard,
                    effect=bound_effect,
                    description=base_rule.description
                )
                model_obj.add_rule(rule_obj)
            elif hasattr(attr, '_is_invariant') and getattr(attr, '_is_invariant'):
                base_inv = getattr(attr, '_invariant_obj')
                bound_predicate = attr
                inv_obj = Invariant(
                    name=base_inv.name,
                    predicate=bound_predicate,
                    description=base_inv.description
                )
                model_obj.add_invariant(inv_obj)

        cls.model = model_obj
        return cls
    return decorator
