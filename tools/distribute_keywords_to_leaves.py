#!/usr/bin/env python3
"""
キーワードを一番下の階層（最深セクション）に分散配置するスクリプト

各セクション内で言及されているキーワードを検出し、
そのセクションの最深の子見出しに付与する。

使い方:
    python3 distribute_keywords_to_leaves.py [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.parent
COMPONENTS_DIR = PROJECT_ROOT / "docs" / "components"

# スキップ対象ファイル
COMPONENT_SKIP = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}

# Template キーワード（除外対象）
TEMPLATE_KW_PATTERN = {
    "Decision_", "Strategy_", "Requirement_", "req_", "concept", "Constraint_"
}


def extract_keywords(text: str) -> set[str]:
    """テキストから {Keyword} を抽出"""
    pattern = r'\{([A-Za-z0-9_]+)\}'
    matches = re.findall(pattern, text)
    # Template キーワード除外
    result = set()
    for m in matches:
        if not any(m.startswith(p) for p in TEMPLATE_KW_PATTERN):
            result.add(m)
    return result


def parse_sections_hierarchical(file_path: Path) -> list:
    """ファイルを解析してセクション階層を構築"""
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    sections = []

    for i, line in enumerate(lines):
        match = re.match(r'^(#{2,4})\s+(.+?)(\s+`\{.+?\}`)*\s*$', line)
        if match:
            depth = len(match.group(1))
            heading_text = match.group(2).strip()
            existing_keywords = extract_keywords(line)

            # このセクションの開始行（見出しの次の行）
            section_start = i + 1

            # このセクションの終了行（次の同レベル以上の見出し）
            section_end = section_start
            while section_end < len(lines):
                next_line = lines[section_end]
                next_match = re.match(r'^(#{2,4})', next_line)
                if next_match and len(next_match.group(1)) <= depth:
                    break
                section_end += 1

            # このセクション内のテキスト
            section_body = '\n'.join(lines[section_start:section_end])
            body_keywords = extract_keywords(section_body)

            sections.append({
                'depth': depth,
                'heading': heading_text,
                'line_num': i + 1,
                'start': section_start,
                'end': section_end,
                'existing_keywords': existing_keywords,
                'body_keywords': body_keywords,
                'children': []
            })

    # 階層構造を構築
    root = {'depth': 1, 'children': []}
    stack = [root]

    for section in sections:
        # スタックから不要な親を取り除く
        while len(stack) > 1 and stack[-1]['depth'] >= section['depth']:
            stack.pop()

        # 現在のセクションを親に追加
        stack[-1]['children'].append(section)
        stack.append(section)

    return root['children']


def assign_keywords_to_leaves(sections: list) -> dict[int, set[str]]:
    """
    セクション階層から、各セクションが持つべきキーワードを決定する。
    一番下の階層（子を持たない）セクションだけにキーワードを付与。
    """
    assignment = {}

    def process_recursive(section):
        # 見出しと本文のキーワードを統合
        all_keywords = section['body_keywords'].copy()

        # 子がある場合
        if section['children']:
            for child in section['children']:
                child_kws = process_recursive(child)
                # 子から返されたキーワードを親に蓄積（子が担当しないキーワード）
                # → 親が担当すべきキーワードを記録

            # このセクションのキーワードは、子が処理した後の残りを持つ
            # 実装上、子が全キーワードを処理するなら、親は空
            # ただし、複雑なので、ここでは「子が持つキーワード」を親から削除
            for child in section['children']:
                all_keywords -= child['body_keywords']

        # このセクションのキーワード確定
        assignment[section['line_num']] = all_keywords

        return all_keywords

    for section in sections:
        process_recursive(section)

    return assignment


def process_file(file_path: Path, dry_run: bool = False) -> int:
    """ファイル内のキーワードを再配置"""
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    # セクション階層を解析
    sections = parse_sections_hierarchical(file_path)

    # 各セクション（見出し行）とそこに付与すべきキーワードのマッピング
    heading_to_keywords = {}

    def collect_headings(secs, parent_keywords=None):
        """セクション階層を巡回してキーワード配置を決定"""
        if parent_keywords is None:
            parent_keywords = set()

        for section in secs:
            # このセクションが担当するキーワード = body のキーワード
            section_keywords = section['body_keywords'].copy()

            # 子がある場合、子が処理するキーワードを除く
            if section['children']:
                child_keywords = set()
                for child in section['children']:
                    child_keywords.update(child['body_keywords'])
                section_keywords -= child_keywords

            heading_to_keywords[section['line_num']] = section_keywords

            # 子を再帰処理
            if section['children']:
                collect_headings(section['children'], section_keywords)

    collect_headings(sections)

    # ファイルを再構築
    modified_count = 0
    new_lines = []

    for i, line in enumerate(lines, 1):
        if i in heading_to_keywords:
            # 見出し行を更新
            keywords = heading_to_keywords[i]

            # 既存のキーワード（見出しから）を削除
            heading_clean = re.sub(r'\s+`\{[^}]+\}`(\s+`\{[^}]+\}`)*\s*$', '', line)

            if keywords:
                # 新しいキーワードを追加
                kw_part = ' '.join(f'`{{{kw}}}`' for kw in sorted(keywords))
                new_line = f"{heading_clean} {kw_part}"
            else:
                new_line = heading_clean

            if new_line != line:
                modified_count += 1
                if not dry_run:
                    print(f"  {file_path.relative_to(PROJECT_ROOT)}:{i}")
                    print(f"    修正前: {line[:70]}")
                    print(f"    修正後: {new_line[:70]}")

            new_lines.append(new_line)
        else:
            new_lines.append(line)

    # ファイルに書き込み
    if modified_count > 0 and not dry_run:
        new_content = '\n'.join(new_lines)
        file_path.write_text(new_content, encoding='utf-8')

    return modified_count


def main():
    parser = argparse.ArgumentParser(
        description="キーワードを最深セクションに分散配置"
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='修正内容を表示するだけで実際には修正しない')
    args = parser.parse_args()

    # 再帰的に全mdファイルを収集
    md_files = list(COMPONENTS_DIR.rglob('*.md'))

    total_modified = 0

    print("=" * 80)
    print("キーワード最深階層配置処理")
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
