"""
reporter.py: 検証結果の Markdown レポートおよび Mermaid 状態遷移図生成モジュール
"""

import json
from typing import Dict, List, Any
from .core import VerificationResult, ResultStatus, State

def generate_markdown_report(result: VerificationResult, model_name: str = "Model") -> str:
    """検証結果を Markdown ドキュメント形式にフォーマット"""
    lines = []
    lines.append(f"# 形式検証結果レポート: `{model_name}`")
    lines.append("")
    
    # 総合判定バッジ
    if result.status == ResultStatus.PASSED:
        lines.append("> [!NOTE]")
        lines.append("> **判定: SUCCESS (PASSED)**")
        lines.append("> すべての不変式 (Invariant) が保持され、デッドロックは検出されませんでした。")
    elif result.status == ResultStatus.INVARIANT_VIOLATED:
        lines.append("> [!CAUTION]")
        lines.append(f"> **判定: FAILED (INVARIANT VIOLATED: `{result.violated_invariant.name if result.violated_invariant else 'Unknown'}`)**")
        lines.append("> 不変式が破られた状態が検出されました。下記の反例トレースを参照してください。")
    elif result.status == ResultStatus.DEADLOCK_DETECTED:
        lines.append("> [!WARNING]")
        lines.append("> **判定: FAILED (DEADLOCK DETECTED)**")
        lines.append("> 有効な遷移ルールが存在しない状態に到達しました。")
    elif result.status == ResultStatus.MAX_STATES_EXCEEDED:
        lines.append("> [!IMPORTANT]")
        lines.append("> **判定: INCOMPLETE (MAX STATES EXCEEDED)**")
        lines.append("> 状態空間探索の上限に達しました。")
        
    lines.append("")
    lines.append("## 検証メトリクス")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("| :--- | :--- |")
    lines.append(f"| **検証ステータス** | `{result.status.name}` |")
    lines.append(f"| **探索状態数 (Explored States)** | {result.states_explored:,} |")
    lines.append(f"| **総遷移数 (Explored Transitions)** | {result.transitions_explored:,} |")
    lines.append(f"| **実行時間** | {result.execution_time_sec:.4f} 秒 |")
    
    if result.violated_invariant:
        lines.append(f"| **違反不変式** | `{result.violated_invariant.name}` ({result.violated_invariant.description}) |")
        
    lines.append("")
    
    # 反例トレース (Counterexample)
    if result.counterexample:
        lines.append("## 反例トレース (Counterexample Execution Trace)")
        lines.append("")
        lines.append("以下のステップ順序で反例状態へ到達しました：")
        lines.append("")
        lines.append("| Step | アクション / ルール | 状態データ (State) |")
        lines.append("| :---: | :--- | :--- |")
        
        for step in result.counterexample:
            act_str = f"`{step.action}`" if step.action else "*Init (初期状態)*"
            st_json = json.dumps(step.state.to_dict(), ensure_ascii=False)
            lines.append(f"| {step.step} | {act_str} | `{st_json}` |")
            
        lines.append("")

    # Mermaid 状態遷移図の埋め込み
    if result.state_graph and len(result.reachable_states) <= 30:
        lines.append("## 状態遷移図 (State Transition Graph)")
        lines.append("")
        lines.append("```mermaid")
        lines.append(generate_mermaid_diagram(result.state_graph, result.counterexample))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def generate_mermaid_diagram(
    state_graph: Dict[State, List[tuple]], 
    counterexample: List[Any] = None
) -> str:
    """状態グラフから Mermaid (graph TD) ダイアグラム表現を自動生成"""
    lines = ["graph TD"]
    
    # 状態ノードの ID マッピング (S0, S1, ...)
    state_to_id: Dict[State, str] = {}
    for idx, st in enumerate(state_graph.keys()):
        state_to_id[st] = f"S{idx}"
        
    cx_edges = set()
    cx_states = set()
    if counterexample:
        for idx in range(len(counterexample) - 1):
            s_from = counterexample[idx].state
            s_to = counterexample[idx + 1].state
            act = counterexample[idx + 1].action
            cx_states.add(s_from)
            cx_states.add(s_to)
            cx_edges.add((s_from, act, s_to))

    # ノード定義
    for st, s_id in state_to_id.items():
        st_dict = st.to_dict()
        label = json.dumps(st_dict, ensure_ascii=False).replace('"', "'")
        if st in cx_states:
            lines.append(f'    {s_id}["{s_id}: {label}"]:::failNode')
        else:
            lines.append(f'    {s_id}["{s_id}: {label}"]')

    # エッジ定義
    for src_state, transitions in state_graph.items():
        src_id = state_to_id.get(src_state)
        if not src_id:
            continue
        for action_name, dst_state in transitions:
            dst_id = state_to_id.get(dst_state)
            if dst_id:
                if (src_state, action_name, dst_state) in cx_edges:
                    lines.append(f'    {src_id} ==>|"{action_name}"| {dst_id}')
                else:
                    lines.append(f'    {src_id} -->|"{action_name}"| {dst_id}')

    # スタイル
    if cx_states:
        lines.append("    classDef failNode fill:#ffdddd,stroke:#ff0000,stroke-width:2px;")
        
    return "\n".join(lines)
