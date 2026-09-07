#!/usr/bin/env python3
"""
collect_chain.py
コンポーネント名または仕様書パスから、4層エビデンスチェーン
（仕様書 → 形式検証 → コンセプトコード → 単体テスト / WIT）
の構成ファイルを自動収集・存在確認するヘルパースクリプト。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TypedDict


class ChainArtifacts(TypedDict):
    component: str
    tier: str
    specification: str | None
    formal: list[str]
    concept: list[str]
    test_spec: list[str]
    wit: list[str]
    benchmark: list[str]
    missing_evidences: list[str]


def parse_evidence_block(content: str) -> dict[str, list[str]]:
    """仕様書ヘッダー内の <!-- evidence: ... --> ブロックを解析する。"""
    evidence_match = re.search(r"<!--\s*evidence:\s*(.*?)\s*-->", content, re.DOTALL | re.IGNORECASE)
    if not evidence_match:
        return {}

    lines = evidence_match.group(1).splitlines()
    result: dict[str, list[str]] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            val = val.strip()
            if val:
                result.setdefault(key, []).append(val)
    return result


def find_component_files(repo_root: Path, target: str) -> ChainArtifacts:
    """指定されたターゲット（名前またはファイルパス）から関連ファイルを探索・収集する。"""
    target_path = Path(target)
    spec_file: Path | None = None

    if target_path.is_file():
        spec_file = target_path if target_path.is_absolute() else (repo_root / target_path).resolve()
    else:
        # パスまたはコンポーネント名として docs/components 内を探索
        target_stem = target_path.stem
        candidates = list(repo_root.glob(f"docs/components/**/{target_stem}.md"))
        if candidates:
            spec_file = candidates[0]
        else:
            candidates = list(repo_root.glob(f"docs/components/**/{target}.md"))
            if candidates:
                spec_file = candidates[0]

    if not spec_file or not spec_file.exists():
        raise FileNotFoundError(f"仕様書が見つかりません: {target}")

    tier_dir = spec_file.parent
    tier_name = tier_dir.name
    component_name = spec_file.stem

    # 仕様書読み込み
    content = spec_file.read_text(encoding="utf-8")
    evidences = parse_evidence_block(content)

    formal_files: list[Path] = []
    concept_files: list[Path] = []
    test_spec_files: list[Path] = []
    wit_files: list[Path] = []
    benchmark_files: list[Path] = []
    missing_evidences: list[str] = []

    # 1. evidence ヘッダーからの収集
    for f in evidences.get("formal", []):
        p = (tier_dir / f).resolve()
        if p.exists():
            formal_files.append(p)
        else:
            missing_evidences.append(f"formal: {f}")

    for c in evidences.get("concept", []):
        p = (tier_dir / c).resolve()
        if p.exists():
            concept_files.append(p)
        else:
            missing_evidences.append(f"concept: {c}")

    for b in evidences.get("benchmark", []):
        p = (tier_dir / b).resolve()
        if p.exists():
            benchmark_files.append(p)
        else:
            missing_evidences.append(f"benchmark: {b}")

    for w in evidences.get("wit", []):
        p = (tier_dir / w).resolve()
        if p.exists():
            wit_files.append(p)
        else:
            missing_evidences.append(f"wit: {w}")

    # 2. ディレクトリ規約に基づく自動補完（ヘッダーに未記載の場合も補足）
    # formal
    if not formal_files:
        for p in tier_dir.glob(f"formal/*{component_name}*.py"):
            if p.is_file():
                formal_files.append(p)
    # concepts
    if not concept_files:
        for p in tier_dir.glob(f"concepts/*{component_name}*.py"):
            if p.is_file():
                concept_files.append(p)
    # tests
    for p in tier_dir.glob(f"tests/*{component_name}*_test_spec.md"):
        if p.is_file():
            test_spec_files.append(p)
    if not test_spec_files:
        for p in tier_dir.glob(f"tests/*{component_name}*.md"):
            if p.is_file():
                test_spec_files.append(p)
    # wit
    if not wit_files:
        for p in tier_dir.glob(f"wit/*{component_name}*.wit"):
            if p.is_file():
                wit_files.append(p)
        if not wit_files and list(tier_dir.glob("wit/*.wit")):
            # 同一 Tier に wit がある場合
            wit_files.extend(tier_dir.glob("wit/*.wit"))

    def to_rel(paths: list[Path]) -> list[str]:
        deduped = sorted(set(paths))
        return [str(p.relative_to(repo_root)).replace("\\", "/") for p in deduped]

    rel_spec = str(spec_file.relative_to(repo_root)).replace("\\", "/")

    return {
        "component": component_name,
        "tier": tier_name,
        "specification": rel_spec,
        "formal": to_rel(formal_files),
        "concept": to_rel(concept_files),
        "test_spec": to_rel(test_spec_files),
        "wit": to_rel(wit_files),
        "benchmark": to_rel(benchmark_files),
        "missing_evidences": missing_evidences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="コンポーネントの垂直エビデンスチェーン収集ツール")
    parser.add_argument("target", help="コンポーネント名（例: os_coos）または仕様書ファイルパス")
    parser.add_argument("--json", action="store_true", help="JSON形式で出力")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[4]  # .agents/skills/document-review/scripts/collect_chain.py -> repo_root

    try:
        data = find_component_files(repo_root, args.target)
    except Exception as e:
        sys.stderr.write(f"エラー: {e}\n")
        return 1

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    print(f"=== Component Verification Chain: {data['component']} ({data['tier']}) ===")
    print(f"  [1. Specification] : {data['specification']}")
    print(f"  [2. Formal Model]  : {', '.join(data['formal']) if data['formal'] else '(None)'}")
    print(f"  [3. Concept Code]  : {', '.join(data['concept']) if data['concept'] else '(None)'}")
    print(f"  [4. Test Spec]     : {', '.join(data['test_spec']) if data['test_spec'] else '(None)'}")
    if data["wit"]:
        print(f"  [Wit Interface]    : {', '.join(data['wit'])}")
    if data["benchmark"]:
        print(f"  [Benchmarks]       : {', '.join(data['benchmark'])}")
    if data["missing_evidences"]:
        print(f"  [! Missing Links]  : {', '.join(data['missing_evidences'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
