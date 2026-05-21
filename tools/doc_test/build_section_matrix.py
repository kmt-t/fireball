#!/usr/bin/env python3
"""
Build a section-wise compatibility matrix across document tiers.

Matches sections between parent (Tier N) and child (Tier N+1) documents
based on keyword overlap and heading similarity.

Output: CSV matrix with review points per section pair.
"""

import re
import csv
import sys
from pathlib import Path
from dataclasses import dataclass
from difflib import SequenceMatcher

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_sections import extract_sections_from_file, Section

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
COMPONENTS_DIR = DOCS_DIR / "components"

KEYWORD_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")


def string_similarity(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def match_sections(parent_sections: list[Section], child_sections: list[Section]) -> list[tuple[Section, Section, float]]:
    """
    Match parent and child sections based on:
    1. Shared keywords (high priority)
    2. Heading similarity (fallback)

    Returns list of (parent_section, child_section, confidence) tuples.
    """
    pairs = []
    matched_children = set()

    # Pass 1: Match by shared keywords
    for parent in parent_sections:
        parent_kws = set(parent.keywords)
        if not parent_kws:
            continue

        for i, child in enumerate(child_sections):
            if i in matched_children:
                continue
            child_kws = set(child.keywords)
            shared = parent_kws.intersection(child_kws)

            if shared:
                confidence = len(shared) / max(len(parent_kws), len(child_kws))
                pairs.append((parent, child, confidence))
                matched_children.add(i)
                break

    # Pass 2: Match remaining sections by heading similarity
    unmatched_parents = [p for p in parent_sections if p not in [pair[0] for pair in pairs]]
    unmatched_children = [child_sections[i] for i in range(len(child_sections)) if i not in matched_children]

    for parent in unmatched_parents:
        best_match = None
        best_sim = 0.5  # Minimum threshold

        for child in unmatched_children:
            sim = string_similarity(parent.heading, child.heading)
            if sim > best_sim:
                best_sim = sim
                best_match = child

        if best_match:
            pairs.append((parent, best_match, best_sim))
            unmatched_children.remove(best_match)

    # Pass 3: Flag unmatched sections
    for parent in unmatched_parents:
        if parent not in [pair[0] for pair in pairs]:
            pairs.append((parent, None, 0.0))

    for child in unmatched_children:
        pairs.append((None, child, 0.0))

    return pairs


def generate_review_points(parent_section: Section | None, child_section: Section | None) -> str:
    """Generate review checklist based on section pair."""
    if not parent_section or not child_section:
        return "⚠ セクション対応なし - 設計漏れまたはドキュメント不整合"

    points = []

    # Check keyword consistency
    if parent_section.keywords and child_section.keywords:
        parent_kws = set(parent_section.keywords)
        child_kws = set(child_section.keywords)
        shared = parent_kws.intersection(child_kws)
        missing = parent_kws - child_kws

        if not shared:
            points.append("❌ キーワード重複なし")
        else:
            points.append(f"✓ キーワード共有: {', '.join(sorted(shared))}")

        if missing:
            points.append(f"⚠ 親レイヤーのキーワードが実装されていない: {', '.join(sorted(missing))}")
    elif parent_section.keywords:
        points.append(f"⚠ 親レイヤーのキーワード {', '.join(parent_section.keywords)} が子レイヤーで引き継がれていない")

    # Generic checks
    points.append("• API/インターフェース整合性: 引数、戻り値の型と説明")
    points.append("• 状態遷移/ライフサイクル: タイミング、プロトコルの一致")
    points.append("• エラーハンドリング: 方針と例外処理の齟齬")

    return "\n".join(points)


def build_matrix_csv(parent_path: Path, child_path: Path, output_path: Path):
    """Build and export section matrix as CSV."""
    parent_sections = extract_sections_from_file(parent_path)
    child_sections = extract_sections_from_file(child_path)

    pairs = match_sections(parent_sections, child_sections)

    # Write CSV
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "親レイヤーセクション",
            "親キーワード",
            "子レイヤーセクション",
            "子キーワード",
            "マッチ信頼度",
            "レビューポイント"
        ])

        for parent, child, confidence in pairs:
            parent_heading = parent.heading if parent else "（対応なし）"
            parent_kws = ", ".join(parent.keywords) if parent else ""
            child_heading = child.heading if child else "（対応なし）"
            child_kws = ", ".join(child.keywords) if child else ""
            confidence_str = f"{confidence:.1%}" if confidence > 0 else "N/A"
            review_points = generate_review_points(parent, child)

            writer.writerow([
                parent_heading,
                parent_kws,
                child_heading,
                child_kws,
                confidence_str,
                review_points
            ])


def build_matrix_markdown(parent_path: Path, child_path: Path, output_path: Path):
    """Build and export section matrix as Markdown."""
    parent_sections = extract_sections_from_file(parent_path)
    child_sections = extract_sections_from_file(child_path)

    pairs = match_sections(parent_sections, child_sections)

    # Write Markdown
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# セクションマトリクス\n\n")
        f.write(f"**親レイヤー:** {parent_path.name}\n")
        f.write(f"**子レイヤー:** {child_path.name}\n\n")

        f.write("| 親セクション | 親キーワード | 子セクション | 子キーワード | マッチ度 |\n")
        f.write("|---|---|---|---|---|\n")

        for parent, child, confidence in pairs:
            parent_heading = f"`{parent.heading}`" if parent else "（対応なし）"
            parent_kws = ", ".join(f"`{{{kw}}}`" for kw in parent.keywords) if parent else ""
            child_heading = f"`{child.heading}`" if child else "（対応なし）"
            child_kws = ", ".join(f"`{{{kw}}}`" for kw in child.keywords) if child else ""
            confidence_str = f"{confidence:.0%}" if confidence > 0 else "—"

            f.write(f"| {parent_heading} | {parent_kws} | {child_heading} | {child_kws} | {confidence_str} |\n")

        f.write("\n## レビューポイント\n\n")
        for idx, (parent, child, confidence) in enumerate(pairs, 1):
            parent_heading = parent.heading if parent else "（対応なし）"
            child_heading = child.heading if child else "（対応なし）"
            review_points = generate_review_points(parent, child)

            f.write(f"### {idx}. {parent_heading} → {child_heading}\n\n")
            f.write(f"**マッチ信頼度:** {confidence:.0%}\n\n")
            f.write(f"{review_points}\n\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build section-wise compatibility matrix")
    parser.add_argument("parent", help="Parent layer document (Tier N)")
    parser.add_argument("child", help="Child layer document (Tier N+1)")
    parser.add_argument("--format", choices=["csv", "markdown"], default="markdown",
                        help="Output format (default: markdown)")
    parser.add_argument("--output", "-o", help="Output file path (auto-generated if not specified)")

    args = parser.parse_args()

    parent_path = Path(args.parent).resolve()
    child_path = Path(args.child).resolve()

    if not parent_path.exists():
        print(f"Parent file not found: {parent_path}")
        sys.exit(1)
    if not child_path.exists():
        print(f"Child file not found: {child_path}")
        sys.exit(1)

    # Generate output path if not specified
    if args.output:
        output_path = Path(args.output)
    else:
        ext = "csv" if args.format == "csv" else "md"
        output_path = Path(f"section_matrix_{parent_path.stem}_vs_{child_path.stem}.{ext}")

    try:
        if args.format == "csv":
            build_matrix_csv(parent_path, child_path, output_path)
        else:
            build_matrix_markdown(parent_path, child_path, output_path)
        print(f"✓ Matrix written to: {output_path}")
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)
