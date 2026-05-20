#!/usr/bin/env python3
"""
仕様書の見出しから要求キーワードを削り、コメントブロックとして本文内に配置するスクリプト

見出し行の末尾にある `{Keyword}` または `^{Keyword}` の記述をすべて削除し、
その見出しの直後に `<!-- traceability: {Keyword1} {Keyword2} ... -->` という
HTMLコメントとして配置し直します。これにより表示上のノイズが完全に除去されます。

使い方:
    python3 .claude/scripts/move_keywords_to_body.py [--dry-run]
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


def extract_heading_keywords(line: str) -> list[str]:
    """見出し行から {Keyword} を抽出"""
    pattern = r'\{([A-Za-z0-9_]+)\}'
    matches = re.findall(pattern, line)
    # Template キーワード除外、重複除外
    result = []
    seen = set()
    for m in matches:
        if not any(m.startswith(p) for p in TEMPLATE_KW_PATTERN):
            if m not in seen:
                result.append(m)
                seen.add(m)
    return result


def clean_heading_line(line: str) -> str:
    """見出しの行末にあるキーワード部分をトリミング"""
    clean_line = line
    while True:
        # 末尾の ` {Keyword}` または ` `{Keyword}`` を削除
        new_clean = re.sub(r'\s+(`\{[A-Za-z0-9_]+\}`|\{[A-Za-z0-9_]+\})\s*$', '', clean_line)
        if new_clean == clean_line:
            break
        clean_line = new_clean
    return clean_line


def process_file(file_path: Path, dry_run: bool = False) -> tuple[int, int]:
    """ファイル内の見出しのキーワードをトリミングして本文の直後に逃がす"""
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    new_lines = []
    modified_headings = 0
    total_keywords_moved = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        # 見出し行か判定
        match = re.match(r'^(#{2,6})\s+(.+)$', line)
        if match:
            heading_prefix = match.group(1)
            heading_text = match.group(2)

            # キーワードの抽出
            keywords = extract_heading_keywords(line)

            if keywords:
                # 見出し行のトリミング
                clean_line = clean_heading_line(line)
                new_lines.append(clean_line)

                # HTML コメントを直後に挿入
                kw_string = ' '.join(f"{{{kw}}}" for kw in keywords)
                comment_line = f"<!-- traceability: {kw_string} -->"
                new_lines.append(comment_line)

                # デバッグ表示
                rel_path = file_path.relative_to(PROJECT_ROOT)
                print(f"[{'DRY-RUN' if dry_run else 'MOVE'}] {rel_path} の見出しをトリミング:")
                print(f"  旧: {line}")
                print(f"  新: {clean_line}")
                print(f"  追加: {comment_line}")

                modified_headings += 1
                total_keywords_moved += len(keywords)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
        i += 1

    if not dry_run and modified_headings > 0:
        file_path.write_text('\n'.join(new_lines), encoding='utf-8')

    return modified_headings, total_keywords_moved


def main():
    parser = argparse.ArgumentParser(description="見出しのキーワード・トリミング＆本文移行ツール")
    parser.add_argument('--dry-run', action='store_true', help='ファイルに書き戻さずシミュレーションのみ実行')
    args = parser.parse_args()

    # markdown ファイルを再帰検索
    md_files = list(COMPONENTS_DIR.rglob('*.md'))

    total_files = 0
    total_headings = 0
    total_keywords = 0

    print("================================================================================")
    print("  仕様書見出しのキーワード・トリミング＆本文移行")
    print("================================================================================\n")

    for md_file in sorted(md_files):
        if md_file.name in COMPONENT_SKIP:
            continue

        headings, keywords = process_file(md_file, args.dry_run)
        if headings > 0:
            total_files += 1
            total_headings += headings
            total_keywords += keywords

    print("\n--------------------------------------------------------------------------------")
    print(f"処理サマリー ({'シミュレーションのみ' if args.dry_run else '完了'})")
    print(f"対象ファイル数:  {total_files} ファイル")
    print(f"修正見出し数:    {total_headings} 件")
    print(f"移動キーワード数:  {total_keywords} 個")
    print("================================================================================")


if __name__ == "__main__":
    main()
