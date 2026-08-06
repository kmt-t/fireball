#!/usr/bin/env python3
"""
DocGraph - Document Graph Extractor & Visualizer

Markdown ドキュメント群から構造 (Document/Section/ID) と参照関係 (Link/Keyword/Hierarchy)
を抽出し、有向グラフ (DocGraph) を構築・可視化する汎用ツール。
"""

import os
import re
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class Node:
    id: str
    label: str
    type: str  # 'file', 'section', 'item'
    file_path: str
    line: int = 0
    metadata: dict = field(default_factory=dict)

@dataclass
class Edge:
    source: str
    target: str
    relation: str  # 'contains', 'refers_to', 'links_to', 'defines'
    metadata: dict = field(default_factory=dict)

@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node):
        if node.id not in self.nodes:
            self.nodes[node.id] = node

    def add_edge(self, edge: Edge):
        # 重複エッジの防止
        for e in self.edges:
            if e.source == edge.source and e.target == edge.target and e.relation == edge.relation:
                return
        self.edges.append(edge)

    def connected_graph(self) -> 'Graph':
        """エッジが存在する（孤立していない）ノードとそのエッジのみを抽出した部分グラフを返す"""
        connected_node_ids = set()
        for e in self.edges:
            connected_node_ids.add(e.source)
            connected_node_ids.add(e.target)

        sub_nodes = {nid: self.nodes[nid] for nid in connected_node_ids if nid in self.nodes}
        sub_edges = [e for e in self.edges if e.source in sub_nodes and e.target in sub_nodes]
        return Graph(nodes=sub_nodes, edges=sub_edges)

    def extract_item_subgraphs(self) -> list[dict]:
        """ID/キーワードノードを中心とした『評価対象サブグラフ』のリストを自動抽出する"""
        subgraphs = []
        item_nodes = [n for n in self.nodes.values() if n.type == "item"]

        for item in item_nodes:
            # 定義元 (defines)
            def_sources = [e.source for e in self.edges if e.target == item.id and e.relation == "defines"]
            # 参照元 (refers_to)
            ref_sources = [e.source for e in self.edges if e.target == item.id and e.relation == "refers_to"]

            # 参照元が存在する場合のみ LLM as a Judge の評価対象とする（参照なしは単体ID）
            if ref_sources:
                subgraphs.append({
                    "item_id": item.id,
                    "item_label": item.label,
                    "defined_in": def_sources,
                    "referenced_in": ref_sources,
                    "total_nodes": 1 + len(def_sources) + len(ref_sources)
                })

        return sorted(subgraphs, key=lambda x: len(x["referenced_in"]), reverse=True)

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges]
        }

    def to_mermaid(self, max_nodes: int = 100) -> str:
        """Graph を Mermaid graph TD 形式の文字列に変換"""
        lines = ["graph TD"]
        
        # クラス定義 (スタイリング)
        lines.append("    classDef fileNode fill:#2d3748,stroke:#4a5568,color:#fff,stroke-width:2px;")
        lines.append("    classDef sectionNode fill:#2b6cb0,stroke:#3182ce,color:#fff;")
        lines.append("    classDef itemNode fill:#d69e2e,stroke:#b7791f,color:#fff,stroke-width:2px;")

        # IDのMermaid安全化マッピング
        safe_id_map = {}
        for idx, nid in enumerate(self.nodes.keys()):
            safe_id_map[nid] = f"N{idx}"

        # ノード定義
        for nid, node in list(self.nodes.items())[:max_nodes]:
            s_id = safe_id_map[nid]
            escaped_label = node.label.replace('"', '\\"').replace('[', '(').replace(']', ')')
            
            if node.type == "file":
                lines.append(f'    {s_id}["[Doc] {escaped_label}"]:::fileNode')
            elif node.type == "section":
                lines.append(f'    {s_id}["[Sec] {escaped_label}"]:::sectionNode')
            elif node.type == "item":
                lines.append(f'    {s_id}["[ID] {escaped_label}"]:::itemNode')

        # エッジ定義
        relation_styles = {
            "contains": "-->",
            "defines": "==>",
            "refers_to": "-.->",
            "links_to": "-->"
        }

        for edge in self.edges:
            if edge.source in safe_id_map and edge.target in safe_id_map:
                s_src = safe_id_map[edge.source]
                s_tgt = safe_id_map[edge.target]
                arrow = relation_styles.get(edge.relation, "-->")
                label = f"|{edge.relation}|" if edge.relation not in ("contains", "defines") else ""
                lines.append(f'    {s_src} {arrow}{label} {s_tgt}')

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Graph Builder / Extractor
# ---------------------------------------------------------------------------

class DocGraphBuilder:
    def __init__(self, kw_pattern: str = r"\{([A-Za-z0-9_\-]+)\}"):
        self.kw_regex = re.compile(kw_pattern)
        self.md_link_regex = re.compile(r'\[([^\]]+)\]\(([^)]+\.md)(#[^)]+)?\)')

    def build_from_directory(self, root_dir: Path) -> Graph:
        graph = Graph()
        md_files = list(root_dir.rglob("*.md"))

        # 1. パス正規化とファイルノード追加
        file_map: dict[Path, str] = {}
        for md_file in md_files:
            rel_path = md_file.relative_to(root_dir).as_posix()
            file_node_id = f"file:{rel_path}"
            file_map[md_file.resolve()] = file_node_id
            graph.add_node(Node(
                id=file_node_id,
                label=rel_path,
                type="file",
                file_path=rel_path
            ))

        # ID/キーワード定義のインデックス
        item_definitions: dict[str, str] = {}  # item_id -> owner_node_id

        # 2. 第1パス: セクション構造と定義ノード (Item) の解析
        for md_file in md_files:
            rel_path = md_file.relative_to(root_dir).as_posix()
            file_node_id = f"file:{rel_path}"
            
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[Warning] Failed to read {md_file}: {e}")
                continue

            lines = content.splitlines()
            current_section_id = file_node_id
            section_stack = [(0, file_node_id)]

            for line_idx, line in enumerate(lines, start=1):
                # 見出しチェック (## H2, ### H3 など)
                heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
                if heading_match:
                    level = len(heading_match.group(1))
                    title = heading_match.group(2).strip()
                    sec_node_id = f"sec:{rel_path}#{title}"
                    
                    graph.add_node(Node(
                        id=sec_node_id,
                        label=title,
                        type="section",
                        file_path=rel_path,
                        line=line_idx
                    ))

                    # 親階層のスタック調整
                    while section_stack and section_stack[-1][0] >= level:
                        section_stack.pop()

                    parent_id = section_stack[-1][1] if section_stack else file_node_id
                    graph.add_edge(Edge(source=parent_id, target=sec_node_id, relation="contains"))
                    
                    section_stack.append((level, sec_node_id))
                    current_section_id = sec_node_id

                # キーワード/ID 定義の検出
                kws = self.kw_regex.findall(line)
                for kw in kws:
                    # テンプレートや共通キーワード除外ルール等の調整も可
                    item_id = f"item:{kw}"
                    graph.add_node(Node(
                        id=item_id,
                        label=f"{{{kw}}}",
                        type="item",
                        file_path=rel_path,
                        line=line_idx
                    ))
                    # 定義元エッジ
                    graph.add_edge(Edge(source=current_section_id, target=item_id, relation="defines"))
                    item_definitions[kw] = item_id

        # 3. 第2パス: 参照 (Link & Keyword Reference) の解析
        for md_file in md_files:
            rel_path = md_file.relative_to(root_dir).as_posix()
            file_node_id = f"file:{rel_path}"
            
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            lines = content.splitlines()
            current_section_id = file_node_id

            for line_idx, line in enumerate(lines, start=1):
                heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
                if heading_match:
                    title = heading_match.group(2).strip()
                    current_section_id = f"sec:{rel_path}#{title}"
                    continue

                # A) Markdown リンクの解析 [text](target.md#anchor)
                for link_text, link_target, anchor in self.md_link_regex.findall(line):
                    # ターゲットファイルのパス解決
                    target_path = (md_file.parent / link_target).resolve()
                    if target_path in file_map:
                        target_file_id = file_map[target_path]
                        target_node_id = target_file_id
                        if anchor:
                            anchor_title = anchor.lstrip('#')
                            target_sec_id = f"sec:{target_file_id.replace('file:', '')}#{anchor_title}"
                            if target_sec_id in graph.nodes:
                                target_node_id = target_sec_id
                        
                        graph.add_edge(Edge(
                            source=current_section_id,
                            target=target_node_id,
                            relation="links_to"
                        ))

                # B) キーワード参照の解析
                kws = self.kw_regex.findall(line)
                for kw in kws:
                    item_id = f"item:{kw}"
                    # 自分が定義した場所でない、参照エッジの追加
                    if item_id in graph.nodes:
                        # 定義元でない場合は refers_to エッジ
                        # (定義元と参照元を区別する)
                        graph.add_edge(Edge(
                            source=current_section_id,
                            target=item_id,
                            relation="refers_to"
                        ))

        return graph


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

import sys

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Extract document graph (DocGraph) from markdown repository.")
    parser.add_argument("root_dir", nargs="?", default="docs", help="Root directory containing markdown files (default: docs)")
    parser.add_argument("--connected-only", action="store_true", help="Filter graph to only nodes with edges")
    parser.add_argument("--subgraphs", action="store_true", help="Extract item-centric evaluation subgraphs for LLM Judge")
    parser.add_argument("--json", action="store_true", help="Output graph as JSON")
    parser.add_argument("--mermaid", action="store_true", help="Output graph in Mermaid format")
    parser.add_argument("--out", type=str, help="Output file path for mermaid or json")
    parser.add_argument("--max-mermaid-nodes", type=int, default=150, help="Max nodes to include in Mermaid output")

    args = parser.parse_args()
    root_path = Path(args.root_dir).resolve()

    if not root_path.exists():
        print(f"Error: Directory '{root_path}' does not exist.")
        return

    builder = DocGraphBuilder()
    graph = builder.build_from_directory(root_path)

    if args.connected_only:
        graph = graph.connected_graph()

    if args.subgraphs:
        subgraphs = graph.extract_item_subgraphs()
        output_str = json.dumps(subgraphs, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(output_str, encoding="utf-8")
            print(f"Extracted {len(subgraphs)} evaluation subgraphs to {args.out}")
        else:
            print(f"=== Found {len(subgraphs)} Evaluation Subgraphs ===")
            for idx, sg in enumerate(subgraphs[:10], start=1):
                print(f"[{idx}] Item: {sg['item_label']} (Refs: {len(sg['referenced_in'])} sections)")
                print(f"     Defined in : {sg['defined_in']}")
                print(f"     Referenced in: {sg['referenced_in'][:3]}{'...' if len(sg['referenced_in']) > 3 else ''}")
        return

    # 統計情報の計算
    total_nodes = len(graph.nodes)
    total_edges = len(graph.edges)
    files_count = sum(1 for n in graph.nodes.values() if n.type == "file")
    secs_count = sum(1 for n in graph.nodes.values() if n.type == "section")
    items_count = sum(1 for n in graph.nodes.values() if n.type == "item")

    summary_text = (
        f"=== DocGraph Extraction Summary ===\n"
        f"Root Path     : {root_path}\n"
        f"Total Nodes   : {total_nodes} (Files: {files_count}, Sections: {secs_count}, Items: {items_count})\n"
        f"Total Edges   : {total_edges}\n"
        f"Connected Only: {args.connected_only}\n"
    )

    if args.json:
        output_str = json.dumps(graph.to_dict(), indent=2, ensure_ascii=False)
    elif args.mermaid:
        output_str = graph.to_mermaid(max_nodes=args.max_mermaid_nodes)
    else:
        output_str = summary_text

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(output_str, encoding="utf-8")
        print(f"Graph written to {out_path}")
        print(summary_text)
    else:
        print(output_str)

if __name__ == "__main__":
    main()
