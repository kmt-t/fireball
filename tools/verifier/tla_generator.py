"""
tla_generator.py: Python DSL モデルから TLA+ (.tla) および TLC 設定 (.cfg) へのトランスパイラ
"""

import inspect
import re
from typing import Dict, List, Any, Optional, Tuple
from .core import State, Rule, Invariant
from .checker import Model

class TLAGenerator:
    """Python DSL の Model から TLA+ コードおよび TLC 設定ファイルを生成するジェネレータ"""
    
    def __init__(self, model: Model):
        self.model = model
        self._infer_variables()

    def _infer_variables(self) -> List[str]:
        """初期状態から状態変数名の一覧を検出"""
        if not self.model.initial_states:
            return []
        init_st = self.model.initial_states[0].to_dict()
        self.variables = sorted(list(init_st.keys()))
        return self.variables

    def _format_tla_val(self, val: Any) -> str:
        """Python の値を TLA+ リテラル文字列に変換"""
        if val is None:
            return '"NULL"'
        elif isinstance(val, bool):
            return "TRUE" if val else "FALSE"
        elif isinstance(val, str):
            return f'"{val}"'
        elif isinstance(val, (int, float)):
            return str(val)
        elif isinstance(val, (list, tuple)):
            elems = ", ".join(self._format_tla_val(x) for x in val)
            return f"<<{elems}>>"
        elif isinstance(val, dict):
            fields = ", ".join(f'{k} :> {self._format_tla_val(v)}' for k, v in val.items())
            return f"[{fields}]"
        return f'"{str(val)}"'

    def generate_init(self) -> str:
        """Init 述語の生成"""
        if not self.model.initial_states:
            return "Init == TRUE"

        # 複数初期状態がある場合は OR 結合
        init_preds = []
        for st in self.model.initial_states:
            st_dict = st.to_dict()
            eqs = [f'{k} = {self._format_tla_val(v)}' for k, v in sorted(st_dict.items())]
            init_preds.append(" /\\ ".join(eqs))

        if len(init_preds) == 1:
            lines = ["Init =="]
            for eq in init_preds[0].split(" /\\ "):
                lines.append(f"    /\\ {eq}")
            return "\n".join(lines)
        else:
            lines = ["Init =="]
            for pred in init_preds:
                lines.append(f"    \\/ ({pred})")
            return "\n".join(lines)

    def _extract_tla_from_rule(self, rule: Rule) -> Tuple[str, str]:
        """ルールの guard と effect から TLA+ 条件式・プライム式を抽出"""
        # 0. 構造的 Transition 情報が存在する場合（StateMachine DSL 経由）
        if hasattr(rule, 'transition_info'):
            t = getattr(rule, 'transition_info')
            guard_preds = [f"{k} = {self._format_tla_val(v)}" for k, v in t.src.items()]
            if t.guard:
                guard_preds.append(self._parse_guard_source(t.guard))
            guard_str = " /\\ ".join(guard_preds) if guard_preds else "TRUE"

            effect_lines = [f"{k}' = {self._format_tla_val(v)}" for k, v in t.dst.items()]
            modified_vars = set(t.dst.keys())
            for v in self.variables:
                if v not in modified_vars:
                    effect_lines.append(f"{v}' = {v}")
            return guard_str, " /\\ ".join(effect_lines)

        # DSL 属性として明示的な tla_guard / tla_effect が指定されている場合
        tla_guard = getattr(rule, 'tla_guard', None)
        tla_effect = getattr(rule, 'tla_effect', None)
        
        # 1. Guard
        if tla_guard:
            guard_str = tla_guard
        else:
            # Python 関数のソースコード解析試行
            guard_str = self._parse_guard_source(rule.guard)

        # 2. Effect
        if tla_effect:
            effect_lines = [f"{k}' = {v}" for k, v in tla_effect.items()]
        else:
            effect_lines = self._parse_effect_source(rule.effect)

        # 変化しない変数に対して unchanged (x' = x) を補完
        modified_vars = set()
        for eff in effect_lines:
            m = re.match(r"^([A-Za-z0-9_]+)'", eff.strip())
            if m:
                modified_vars.add(m.group(1))

        for v in self.variables:
            if v not in modified_vars:
                effect_lines.append(f"{v}' = {v}")

        return guard_str, " /\\ ".join(effect_lines)

    def _parse_guard_source(self, guard_func) -> str:
        """ラムダ式等のソースコードから TLA+ guard へのフォールバック変換"""
        try:
            src = inspect.getsource(guard_func).strip()
            # lambda s: s['x'] < 5 などの抽象解析
            m = re.search(r"lambda\s+([A-Za-z0-9_]+)\s*:\s*(.+)$", src)
            if m:
                var_name = m.group(1)
                body = m.group(2).rstrip(",)")
                # s['key'] -> key
                tla_body = re.sub(rf"{var_name}\['([A-Za-z0-9_]+)'\]", r"\1", body)
                tla_body = re.sub(rf"{var_name}\.get\('([A-Za-z0-9_]+)'\)", r"\1", tla_body)
                # Python 演算子 -> TLA+ 演算子
                tla_body = tla_body.replace(" and ", " /\\ ").replace(" or ", " \\/ ").replace(" not ", " ~ ")
                tla_body = tla_body.replace("==", "=").replace("None", '"NULL"')
                return tla_body
        except Exception:
            pass
        return "TRUE"

    def _parse_effect_source(self, effect_func) -> List[str]:
        """effect 関数のソースコードから TLA+ プライム代入文への解析"""
        effect_lines = []
        try:
            src = inspect.getsource(effect_func).strip()
            # s.set('k', v) または s.update(k=v) のパターン抽出
            updates = re.findall(r"(?:set|update)\s*\(\s*['\"]?([A-Za-z0-9_]+)['\"]?\s*[=,]\s*([^)]+)\)", src)
            for var_name, expr in updates:
                expr = expr.strip().strip("'\"")
                # s['key'] -> key
                expr_tla = re.sub(r"[A-Za-z0-9_]+\['([A-Za-z0-9_]+)'\]", r"\1", expr)
                expr_tla = expr_tla.replace("==", "=").replace("None", '"NULL"')
                effect_lines.append(f"{var_name}' = {expr_tla}")
        except Exception:
            pass
        return effect_lines

    def generate_tla(self) -> str:
        """完全な TLA+ 仕様 (.tla) コードの出力"""
        module_name = self.model.name
        lines = []
        lines.append(f"---- MODULE {module_name} ----")
        lines.append("EXTENDS Naturals, Sequences, FiniteSets, TLC")
        lines.append("")
        
        # 変数宣言
        vars_str = ", ".join(self.variables)
        lines.append(f"VARIABLES {vars_str}")
        lines.append(f"vars == <<{vars_str}>>")
        lines.append("")
        
        # Init 述語
        lines.append(self.generate_init())
        lines.append("")
        
        # 各ルールの述語生成
        rule_names = []
        for r in self.model.rules:
            r_name = r.name.replace(" ", "_")
            rule_names.append(r_name)
            guard_tla, effect_tla = self._extract_tla_from_rule(r)
            
            lines.append(f"{r_name} ==")
            lines.append(f"    /\\ {guard_tla}")
            for eff in effect_tla.split(" /\\ "):
                lines.append(f"    /\\ {eff}")
            lines.append("")

        # Next 述語
        lines.append("Next ==")
        if rule_names:
            for idx, r_name in enumerate(rule_names):
                prefix = "    \\/ " if idx == 0 else "    \\/ "
                lines.append(f"{prefix}{r_name}")
        else:
            lines.append("    FALSE")
        lines.append("")

        # Spec 述語
        lines.append("Spec == Init /\\ [][Next]_vars")
        lines.append("")

        # Invariant 述語の生成
        for inv in self.model.invariants:
            inv_name = inv.name.replace(" ", "_")
            tla_pred = getattr(inv, 'tla_predicate', None)
            if not tla_pred:
                tla_pred = self._parse_guard_source(inv.predicate)
            lines.append(f"{inv_name} ==")
            lines.append(f"    {tla_pred}")
            lines.append("")

        lines.append("====")
        return "\n".join(lines)

    def generate_cfg(self) -> str:
        """TLC 設定ファイル (.cfg) の出力"""
        lines = ["SPECIFICATION Spec"]
        
        # デッドロックチェック設定
        if self.model.allow_deadlock:
            lines.append("CHECK_DEADLOCK FALSE")
        else:
            lines.append("CHECK_DEADLOCK TRUE")
            
        # 不変式の登録
        for inv in self.model.invariants:
            inv_name = inv.name.replace(" ", "_")
            lines.append(f"INVARIANT {inv_name}")
            
        return "\n".join(lines)
