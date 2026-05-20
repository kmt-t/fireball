#!/usr/bin/env python3
"""
各セクション（##、###、####）にそのセクション内のキーワードを付与するスクリプト

各セクションは独立して、そのセクション内で言及されているキーワードだけを見出しに付与する。
親セクションのキーワードは引き継がない。

使い方:
    python3 assign_keywords_per_section.py [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
COMPONENTS_DIR = PROJECT_ROOT / "docs" / "components"

# スキップ対象ファイル
COMPONENT_SKIP = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}

# Template キーワード（除外対象）
TEMPLATE_KW_PATTERN = {
    "Decision_", "Strategy_", "Requirement_", "req_", "concept", "Constraint_"
}


def extract_keywords(text: str) -> list[str]:
    """テキストから {Keyword} を抽出"""
    pattern = r'\{([A-Za-z0-9_]+)\}'
    matches = re.findall(pattern, text)
    # Template キーワード除外、重複除外
    result = []
    seen = set()
    for m in matches:
        if not any(m.startswith(p) for p in TEMPLATE_KW_PATTERN):
            if m not in seen:
                result.append(m)
                seen.add(m)
    return result


def process_file(file_path: Path, dry_run: bool = False) -> int:
    """ファイル内の各セクションにキーワードを付与"""
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    # 見出しと範囲を抽出
    sections = []
    for i, line in enumerate(lines):
        match = re.match(r'^(#{2,4})\s+(.+?)(\s+`\{.+?\}`)*\s*$', line)
        if match:
            depth = len(match.group(1))
            heading = match.group(2).strip()
            sections.append({
                'line_idx': i,
                'depth': depth,
                'heading': heading,
                'start': i + 1,
                'end': None
            })

    # 各セクションの終了行を確定
    for idx, section in enumerate(sections):
        if idx + 1 < len(sections):
            # 次のセクションの開始が終了点
            section['end'] = sections[idx + 1]['line_idx']
        else:
            # 最後のセクション
            section['end'] = len(lines)

    # 各セクションのキーワードを決定
    modified_count = 0
    new_lines = lines.copy()

    for idx, section in enumerate(sections):
        # セクション内のテキスト
        section_text = '\n'.join(lines[section['start']:section['end']])

        # そのセクション内で言及されているキーワード
        keywords = extract_keywords(section_text)

        # このセクションがテキスト内容を持たない場合（図・表だけ）、親のキーワードを継承
        if not keywords and section['depth'] > 2:
            # 親セクション（depth が1小さい）を探す
            parent_depth = section['depth'] - 1
            for prev_idx in range(idx - 1, -1, -1):
                if sections[prev_idx]['depth'] == parent_depth:
                    # 親セクションのキーワードを抽出して継承
                    parent_text = '\n'.join(lines[sections[prev_idx]['start']:section['line_idx']])
                    parent_keywords = extract_keywords(parent_text)
                    keywords = parent_keywords
                    break

        # 見出し行を更新
        old_heading = new_lines[section['line_idx']]

        # 見出しから既存のキーワードを削除
        heading_clean = re.sub(r'\s+`\{[^}]+\}`(\s+`\{[^}]+\}`)*\s*$', '', old_heading)

        # 新しいキーワードを追加
        if keywords:
            kw_part = ' '.join(f'`{{{kw}}}`' for kw in keywords)
            new_heading = f"{heading_clean} {kw_part}"
        else:
            new_heading = heading_clean

        if new_heading != old_heading:
            modified_count += 1
            new_lines[section['line_idx']] = new_heading

            if not dry_run:
                rel_path = file_path.relative_to(PROJECT_ROOT)
                print(f"  {rel_path}:{section['line_idx']+1}")
                print(f"    修正前: {old_heading[:70]}")
                print(f"    修正後: {new_heading[:70]}")

    # ファイルに書き込み
    if modified_count > 0 and not dry_run:
        new_content = '\n'.join(new_lines)
        file_path.write_text(new_content, encoding='utf-8')

    return modified_count


def main():
    parser = argparse.ArgumentParser(
        description="各セクションにそのセクション内のキーワードを付与"
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='修正内容を表示するだけで実際には修正しない')
    args = parser.parse_args()

    # 再帰的に全mdファイルを収集
    md_files = list(COMPONENTS_DIR.rglob('*.md'))

    total_modified = 0

    print("=" * 80)
    print("セクション別キーワード付与処理")
    print("=" * 80)
    if args.dry_run:
        print("[DRY-RUN モード] 実際の修正は行いません\n")
    else:
        print()

    for md_file in sorted(md_files):
        # スキップ判定
        if md_file.name in COMPONENT_SKIP:
            continue

        try:
            modified = process_file(md_file, args.dry_run)
            if modified > 0:
                rel_path = md_file.relative_to(PROJECT_ROOT)
                print(f"\n✓ {rel_path} ({modified} セクション修正)")
                total_modified += modified
        except Exception as e:
            print(f"[ERROR] {md_file}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

    print()
    print("=" * 80)
    print(f"処理完了: {total_modified} セクション修正")
    print("=" * 80)

    if args.dry_run:
        print("\n※ --dry-run モードでした。実際に修正するには --dry-run を外して実行してください。")


if __name__ == "__main__":
    main()
