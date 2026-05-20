#!/usr/bin/env python3
"""
コンポーネント仕様のキーワードを見出し行に統一するスクリプト

各セクション内（body）に散在する {Keyword} を検出し、
見出し行に集約して整理する。

使い方:
    python3 consolidate_keywords_to_heading.py [--dry-run]
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
    """ファイル内のキーワードを見出し行に統一"""
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    modified_count = 0
    new_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # 見出し行か判定
        match = re.match(r'^(#{2,4})\s+(.+?)(\s+`.+?`)*\s*$', line)
        if match:
            heading_prefix = match.group(1)
            heading_text = match.group(2).strip()

            # 見出し行から既存キーワードを抽出
            existing_keywords = extract_keywords(line)

            # 次のセクション（同レベル以上の見出し）までを body として収集
            body_start = i + 1
            body_end = body_start
            heading_depth = len(heading_prefix)

            while body_end < len(lines):
                next_line = lines[body_end]
                # 同レベル以上の見出しが来たら終了
                next_match = re.match(r'^(#{2,4})', next_line)
                if next_match and len(next_match.group(1)) <= heading_depth:
                    break
                body_end += 1

            # body のテキスト
            body_text = '\n'.join(lines[body_start:body_end])

            # body から直接キーワードを抽出
            body_keywords = extract_keywords(body_text)

            # 見出しに無いキーワードを抽出
            new_keywords = [kw for kw in body_keywords if kw not in existing_keywords]

            # 見出し行を更新
            if new_keywords:
                # 見出し行の末尾が既にキーワードで終わっているか確認
                if existing_keywords:
                    # 既存キーワードの直前まで取得
                    heading_without_kw = re.sub(r'\s+`\{[^}]+\}`(\s+`\{[^}]+\}`)*\s*$', '', line)
                    all_keywords = existing_keywords + new_keywords
                else:
                    heading_without_kw = line.rstrip()
                    all_keywords = new_keywords

                # キーワード部を構築
                kw_part = ' '.join(f'`{{{kw}}}`' for kw in all_keywords)
                new_line = f"{heading_without_kw} {kw_part}"

                new_lines.append(new_line)
                modified_count += 1
                if not dry_run:
                    print(f"  {file_path.relative_to(PROJECT_ROOT)}:{i+1}")
                    print(f"    修正前: {line}")
                    print(f"    修正後: {new_line}")
            else:
                new_lines.append(line)

            # body の行をすべて追加し、その中からキーワードを削除
            for body_line in lines[body_start:body_end]:
                # body からキーワードを削除（見出しに集約したため）
                cleaned_line = re.sub(r'\s+`\{([A-Za-z0-9_]+)\}`', '', body_line)
                new_lines.append(cleaned_line)

            i = body_end
        else:
            new_lines.append(line)
            i += 1

    # ファイルに書き込み
    if modified_count > 0 and not dry_run:
        new_content = '\n'.join(new_lines)
        file_path.write_text(new_content, encoding='utf-8')

    return modified_count


def main():
    parser = argparse.ArgumentParser(
        description="キーワードを見出し行に統一"
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='修正内容を表示するだけで実際には修正しない')
    args = parser.parse_args()

    # 再帰的に全mdファイルを収集
    md_files = list(COMPONENTS_DIR.rglob('*.md'))

    total_modified = 0

    print("=" * 80)
    print("キーワード見出し統一処理")
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

    print()
    print("=" * 80)
    print(f"処理完了: {total_modified} セクション修正")
    print("=" * 80)

    if args.dry_run:
        print("\n※ --dry-run モードでした。実際に修正するには --dry-run を外して実行してください。")


if __name__ == "__main__":
    main()
