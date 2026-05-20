#!/usr/bin/env python3
"""
親セクション（##）のキーワードを子セクション（###）に再配置するスクリプト

親セクション（##）が持つキーワードを、その直下の子セクション（###）に分配する。
親セクション自体はキーワードを持たない。

使い方:
    python3 push_keywords_down.py [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
COMPONENTS_DIR = PROJECT_ROOT / "docs" / "components"

# スキップ対象ファイル
COMPONENT_SKIP = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}


def extract_keywords(text: str) -> list[str]:
    """テキストから {Keyword} を抽出"""
    pattern = r'\{([A-Za-z0-9_]+)\}'
    matches = re.findall(pattern, text)
    # 重複除外
    result = []
    seen = set()
    for m in matches:
        if m not in seen:
            result.append(m)
            seen.add(m)
    return result


def process_file(file_path: Path, dry_run: bool = False) -> int:
    """親セクションのキーワードを子セクションに再配置"""
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    new_lines = lines.copy()
    modified_count = 0

    i = 0
    while i < len(new_lines):
        line = new_lines[i]

        # ## 見出し（親セクション）か判定
        match = re.match(r'^##\s+(.+?)(\s+`\{.+?\}`)*\s*$', line)
        if match:
            parent_heading = match.group(1).strip()
            parent_keywords = extract_keywords(line)

            if not parent_keywords:
                i += 1
                continue

            # 親セクションのキーワードを削除
            parent_clean = re.sub(r'\s+`\{[^}]+\}`(\s+`\{[^}]+\}`)*\s*$', '', line)
            new_lines[i] = parent_clean

            # 次の ### を探す
            j = i + 1
            first_child = True
            while j < len(new_lines):
                next_line = new_lines[j]

                # ## が来たら終了
                if re.match(r'^##\s+', next_line):
                    break

                # ### か判定
                if re.match(r'^###\s+', next_line):
                    # 子セクションの見出しにキーワードを付与
                    if first_child:
                        # 最初の子に親のキーワードを全部付与
                        child_keywords = extract_keywords(next_line) + parent_keywords
                    else:
                        # 2番目以降は元々のキーワードだけ
                        child_keywords = extract_keywords(next_line)

                    # 重複除外
                    child_keywords = list(dict.fromkeys(child_keywords))

                    # 見出しを更新
                    child_clean = re.sub(r'\s+`\{[^}]+\}`(\s+`\{[^}]+\}`)*\s*$', '', next_line)
                    if child_keywords:
                        kw_part = ' '.join(f'`{{{kw}}}`' for kw in child_keywords)
                        new_heading = f"{child_clean} {kw_part}"
                    else:
                        new_heading = child_clean

                    if new_heading != next_line:
                        modified_count += 1
                        if not dry_run:
                            print(f"  {file_path.relative_to(PROJECT_ROOT)}:{j+1}")
                            print(f"    修正前: {next_line[:70]}")
                            print(f"    修正後: {new_heading[:70]}")

                    new_lines[j] = new_heading
                    first_child = False

                j += 1

            if new_lines[i] != line:
                modified_count += 1
                if not dry_run:
                    print(f"  {file_path.relative_to(PROJECT_ROOT)}:{i+1}")
                    print(f"    修正前: {line[:70]}")
                    print(f"    修正後: {new_lines[i][:70]}")

        i += 1

    # ファイルに書き込み
    if modified_count > 0 and not dry_run:
        new_content = '\n'.join(new_lines)
        file_path.write_text(new_content, encoding='utf-8')

    return modified_count


def main():
    parser = argparse.ArgumentParser(
        description="親セクションのキーワードを子セクションに再配置"
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='修正内容を表示するだけで実際には修正しない')
    args = parser.parse_args()

    # 再帰的に全mdファイルを収集
    md_files = list(COMPONENTS_DIR.rglob('*.md'))

    total_modified = 0

    print("=" * 80)
    print("親セクションキーワード → 子セクション再配置処理")
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
                print(f"\n✓ {rel_path} ({modified} 行修正)")
                total_modified += modified
        except Exception as e:
            print(f"[ERROR] {md_file}: {e}", file=sys.stderr)

    print()
    print("=" * 80)
    print(f"処理完了: {total_modified} 行修正")
    print("=" * 80)

    if args.dry_run:
        print("\n※ --dry-run モードでした。実際に修正するには --dry-run を外して実行してください。")


if __name__ == "__main__":
    main()
