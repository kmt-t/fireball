#!/usr/bin/env python3
"""
Fireball コンポーネント仕様 × 要求キーワード トレーサビリティ監査スクリプト

章節項（##/###/####）レベルでコンポーネント仕様と要求キーワードの紐付けを検証し、
以下を検出する：
  - S2: 出所不明セクション（キーワード未紐付け）
  - S3: 要求漏れ（セクション未紐付けキーワード）
  - L1: 意味的不整合（--llmで実行）

使い方:
    python3 .claude/scripts/traceability_audit.py [OPTIONS]

オプション:
    (なし)       機械的チェック S2/S3 を実行
    --llm        LLM による意味整合チェック L1 を追加実行
    --model M    LLM モデルを指定（--llm に適用）
    --verbose    S1 マッピング詳細を表示
    --debug      LLM 生レスポンス等のデバッグログを表示

LLM バックエンド（優先順位順）:
    1. SAKURA_AI_API_KEY → Sakura AI (gpt-oss-120b)
    2. OPEN_ROUTER_API_KEY → OpenRouter (google/gemini-3.1-pro-preview)
    3. なし → ollama (localhost:11434, qwen2.5-coder:3b)

生成ファイル:
    docs/components/traceability_matrix.csv       セクション×キーワード マッピング
    tmp/traceability_YYYYMMDD_HHMMSS.txt         コンソール出力ログ
"""

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    requests = None

# ─────────────────────────────────────────────────────────────────────────────
# 定数・設定
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent.parent
REQUIRES_DIR = PROJECT_ROOT / "docs" / "requires"
COMPONENTS_DIR = PROJECT_ROOT / "docs" / "components"
OUTPUT_CSV = COMPONENTS_DIR / "traceability_matrix.csv"
TMP_DIR = PROJECT_ROOT / "tmp"

# スキップ対象ファイル
COMPONENT_SKIP = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}

# S2で免除する見出し（FORMAT.md で定義された構造的なセクション）
STRUCTURAL_HEADINGS = {
    # FORMAT.md の標準セクション
    "概要", "コンセプト", "1. コンセプト",
    "静的モデル", "2. 静的モデル",
    "データ構造", "2.1 データ構造",
    "内部ブロック図", "2.2 内部ブロック図",
    "主要なクラス・構造体・配列・定数", "2.3 主要なクラス・構造体・配列・定数",
    "動的モデル", "3. 動的モデル",
    "アルゴリズム", "3.1 アルゴリズム",
    "状態遷移図", "3.2 状態遷移図",
    "状態遷移", "4.2 状態遷移",
    "内部シーケンス", "3.3 内部シーケンス",
    "インターフェイス定義", "4. インターフェイス定義",
    "インターフェイス設計", "5. インターフェイス設計",
    "公開API", "4.1 公開API", "5.1 公開API",
    "URI/IPCインターフェイス", "4.2 URI/IPCインターフェイス", "5.2 URI/IPCインターフェイス",
    "制約達成の方策", "5. 制約達成の方策", "6. 制約達成の方策",
    "性能制約と方策", "5.1 性能制約と方策", "6.1 性能制約と方策",
    # その他の構造的見出し
    "用語", "用語定義", "用語集",
    "参考", "参考実装", "参考実装リスト", "参考資料",
    "変更履歴", "履歴",
    "命名規則", "命名規約",
    "設計判断", "設計判断の記録", "ADR",
    "フィードバック", "制限事項", "トレードオフ",
    "検証", "6. 検証"
}

# Template キーワード（除外対象）
TEMPLATE_KW_PATTERN = {
    "Decision_", "Strategy_", "Requirement_", "req_", "concept", "Constraint_"
}

# LLM 設定
SAKURA_AI_API_KEY = os.getenv("SAKURA_AI_API_KEY", "")
OPEN_ROUTER_API_KEY = os.getenv("OPEN_ROUTER_API_KEY", "")

USE_SAKURA = bool(SAKURA_AI_API_KEY)
USE_OPENROUTER = bool(OPEN_ROUTER_API_KEY) and not USE_SAKURA

SAKURA_MODEL = "gpt-oss-120b"
OPEN_ROUTER_MODEL = "google/gemini-3.1-pro-preview"
CHECK_MODEL = "qwen2.5-coder:3b"

# ─────────────────────────────────────────────────────────────────────────────
# データ構造
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SectionEntry:
    """コンポーネント仕様のセクション"""
    file: Path
    depth: int  # 2=##, 3=###, 4=####
    heading: str  # 見出しテキスト
    body: str  # セクション本文
    keywords: list[str] = field(default_factory=list)
    line_start: int = 0

    def has_content(self) -> bool:
        """実質的な内容があるか（50文字以上）"""
        return len(self.body.strip()) >= 50

    def is_structural(self) -> bool:
        """構造的セクションか（S2で免除）"""
        for exempt in STRUCTURAL_HEADINGS:
            if exempt.lower() in self.heading.lower():
                return True
        return False


@dataclass
class RequirementEntry:
    """要求仕様のキーワード"""
    keyword: str
    description: str
    category: str  # requirement_list.md 内のセクション


@dataclass
class IssueReport:
    """チェック結果"""
    level: str  # "NG", "WARN", "PASS"
    check_type: str  # "S2", "S3", "L1"
    file: Optional[Path] = None
    line: Optional[int] = None
    heading: Optional[str] = None
    message: str = ""
    detail: str = ""


class Tee:
    """stdout をファイルに同時出力"""
    def __init__(self, file_path: Path):
        self.file = open(file_path, 'w', encoding='utf-8')
        self.stdout = sys.stdout

    def write(self, data):
        self.stdout.write(data)
        # ANSIエスケープ除去版をファイルに出力
        clean = re.sub(r'\x1b\[[0-9;]*m', '', data)
        self.file.write(clean)

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
# ファイル解析
# ─────────────────────────────────────────────────────────────────────────────

def load_requirement_keywords() -> dict[str, RequirementEntry]:
    """requirement_list.md からキーワード定義を抽出"""
    req_file = REQUIRES_DIR / "requirement_list.md"
    if not req_file.exists():
        print(f"[WARN] {req_file} が見つかりません", file=sys.stderr)
        return {}

    keywords = {}
    content = req_file.read_text(encoding='utf-8')

    # | `{Keyword}` | description | ... の形式を抽出
    pattern = r'\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|\s*([^|]+?)\s*\|'
    for match in re.finditer(pattern, content):
        kw_name = match.group(1)
        description = match.group(2).strip()

        # Template キーワードはスキップ
        if any(kw_name.startswith(prefix) for prefix in TEMPLATE_KW_PATTERN):
            continue

        keywords[kw_name] = RequirementEntry(
            keyword=kw_name,
            description=description,
            category="unknown"
        )

    return keywords


def extract_keywords(text: str) -> list[str]:
    """テキストから {Keyword} を抽出"""
    pattern = r'\{([A-Za-z0-9_]+)\}'
    matches = re.findall(pattern, text)
    # Template キーワード除外
    return [m for m in matches
            if not any(m.startswith(p) for p in TEMPLATE_KW_PATTERN)]


def parse_sections(file_path: Path) -> list[SectionEntry]:
    """コンポーネント仕様ファイルをセクション単位で解析"""
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    sections = []
    current_depth = 0
    section_stack = []  # (depth, SectionEntry)

    for i, line in enumerate(lines, 1):
        # 見出し行か判定
        match = re.match(r'^(#{2,4})\s+(.+)$', line)
        if not match:
            continue

        depth = len(match.group(1))
        heading = match.group(2).strip()

        # 現在のセクション（スタックトップ）の本文を確定
        if section_stack and depth <= section_stack[-1][0]:
            # 同レベル以上の見出しが来た→前のセクション終了
            while section_stack and depth <= section_stack[-1][0]:
                prev_depth, prev_section = section_stack.pop()
                sections.append(prev_section)

        # 新しいセクション作成
        section = SectionEntry(
            file=file_path,
            depth=depth,
            heading=heading,
            body="",
            line_start=i
        )

        # スタックに追加
        section_stack.append((depth, section))

    # 残り全てを確定
    for depth, section in section_stack:
        sections.append(section)

    # 各セクションの本文を収集
    for idx, section in enumerate(sections):
        start = section.line_start
        # 次のセクションの開始行、またはファイル終端
        if idx + 1 < len(sections):
            end = sections[idx + 1].line_start
        else:
            end = len(lines)

        body_lines = lines[start:end - 1]  # 見出し自体は除外
        section.body = '\n'.join(body_lines)
        # 見出しと本文の両方からキーワードを抽出
        heading_keywords = extract_keywords(section.heading)
        body_keywords = extract_keywords(section.body)
        section.keywords = list(dict.fromkeys(heading_keywords + body_keywords))  # 重複除外

    return sections


def load_all_sections() -> list[SectionEntry]:
    """全コンポーネント仕様ファイルからセクションを抽出"""
    sections = []

    # 再帰的に全mdファイルを収集
    md_files = list(COMPONENTS_DIR.rglob('*.md'))

    for md_file in sorted(md_files):
        # スキップ判定
        if md_file.name in COMPONENT_SKIP:
            continue

        try:
            file_sections = parse_sections(md_file)
            sections.extend(file_sections)
        except Exception as e:
            print(f"[ERROR] {md_file} 解析失敗: {e}", file=sys.stderr)

    return sections


# ─────────────────────────────────────────────────────────────────────────────
# チェック処理
# ─────────────────────────────────────────────────────────────────────────────

def check_s2_orphan_sections(sections: list[SectionEntry]) -> list[IssueReport]:
    """S2: 出所不明セクション（キーワード未紐付け）を検出"""
    issues = []

    for section in sections:
        # 条件: キーワードなし AND 実質内容あり AND 非構造セクション
        if (not section.keywords and
            section.has_content() and
            not section.is_structural()):

            rel_path = section.file.relative_to(PROJECT_ROOT)
            issue = IssueReport(
                level="NG",
                check_type="S2",
                file=rel_path,
                line=section.line_start,
                heading=section.heading,
                message=f"キーワード未紐付けセクション",
                detail=section.body[:200]
            )
            issues.append(issue)

    return issues


def check_s3_uncovered_requirements(
    requirements: dict[str, RequirementEntry],
    sections: list[SectionEntry]
) -> list[IssueReport]:
    """S3: 要求漏れ（セクション未紐付けキーワード）を検出"""
    issues = []

    # 全セクションで言及されるキーワード集合
    covered_kw = set()
    for section in sections:
        covered_kw.update(section.keywords)

    # 要求キーワードで言及されないもの
    for kw_name, req in requirements.items():
        if kw_name not in covered_kw:
            issue = IssueReport(
                level="WARN",
                check_type="S3",
                message=f"要求キーワード未紐付け: {{{kw_name}}}",
                detail=req.description
            )
            issues.append(issue)

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# LLM チェック
# ─────────────────────────────────────────────────────────────────────────────

def llm_call(prompt: str, model: Optional[str] = None, debug: bool = False) -> str:
    """LLMを呼び出して結果を取得（check_consistency.py パターン）"""
    if USE_SAKURA:
        return _llm_sakura(prompt, model or SAKURA_MODEL, debug)
    elif USE_OPENROUTER:
        return _llm_openrouter(prompt, model or OPEN_ROUTER_MODEL, debug)
    else:
        return _llm_ollama(prompt, model or CHECK_MODEL, debug)


def _llm_sakura(prompt: str, model: str, debug: bool) -> str:
    """Sakura AI API を使用"""
    if not requests:
        return "[LLM不可: requests未インストール]"

    url = "https://api.ai.sakura.ad.jp/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {SAKURA_AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 500,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"]
        if debug:
            print(f"[DEBUG LLM] {result}", file=sys.stderr)
        return result
    except Exception as e:
        return f"[LLM呼び出し失敗: {e}]"


def _llm_openrouter(prompt: str, model: str, debug: bool) -> str:
    """OpenRouter API を使用"""
    if not requests:
        return "[LLM不可: requests未インストール]"

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPEN_ROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 500,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"]
        if debug:
            print(f"[DEBUG LLM] {result}", file=sys.stderr)
        return result
    except Exception as e:
        return f"[LLM呼び出し失敗: {e}]"


def _llm_ollama(prompt: str, model: str, debug: bool) -> str:
    """ollama (localhost:11434) を使用"""
    if not requests:
        return "[LLM不可: requests未インストール]"

    url = "http://localhost:11434/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 500,
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        body = response.json()
        result = body.get("response", "")
        if debug:
            print(f"[DEBUG LLM] {result}", file=sys.stderr)
        return result
    except Exception as e:
        return f"[LLM呼び出し失敗: {e}]"


def check_l1_semantic_alignment(
    sections: list[SectionEntry],
    requirements: dict[str, RequirementEntry],
    model: Optional[str] = None,
    debug: bool = False
) -> list[IssueReport]:
    """L1: セクション内容と要求キーワードの意味的整合性を検証"""
    issues = []

    for section in sections:
        if not section.keywords:
            continue

        # 要求説明を集める
        req_descriptions = []
        for kw in section.keywords:
            if kw in requirements:
                req_descriptions.append(
                    f"  {{{kw}}}: {requirements[kw].description}"
                )

        if not req_descriptions:
            continue

        # LLMプロンプト構築
        prompt = (
            f"コンポーネント仕様のセクション「{section.heading}」の内容が、"
            f"以下の要求キーワード {section.keywords} の定義と意味的に整合するか判定してください。\n\n"
            f"[セクション本文（最初の800文字）]\n"
            f"{section.body[:800]}\n\n"
            f"[対応する要求定義]\n"
            + "\n".join(req_descriptions) + "\n\n"
            "判定: PASS / FAIL / UNCERTAIN\n"
            "理由: （1行で）"
        )

        result = llm_call(prompt, model, debug).strip()

        # 結果パース
        if "FAIL" in result.upper():
            level = "FAIL"
        elif "PASS" in result.upper():
            level = "PASS"
        else:
            level = "UNCERTAIN"

        rel_path = section.file.relative_to(PROJECT_ROOT)
        issue = IssueReport(
            level=level,
            check_type="L1",
            file=rel_path,
            line=section.line_start,
            heading=section.heading,
            message=f"意味的整合チェック: {section.keywords}",
            detail=result[:200]
        )
        issues.append(issue)

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# 出力・報告
# ─────────────────────────────────────────────────────────────────────────────

def write_csv_matrix(
    sections: list[SectionEntry],
    requirements: dict[str, RequirementEntry]
):
    """トレーサビリティマトリクス CSV を生成"""
    TMP_DIR.mkdir(exist_ok=True)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'file', 'section_depth', 'heading', 'keywords',
            'has_keyword', 'line_start', 'body_snippet'
        ])
        writer.writeheader()

        for section in sections:
            writer.writerow({
                'file': str(section.file.relative_to(PROJECT_ROOT)),
                'section_depth': section.depth,
                'heading': section.heading,
                'keywords': '|'.join(section.keywords) if section.keywords else '',
                'has_keyword': 'YES' if section.keywords else 'NO',
                'line_start': section.line_start,
                'body_snippet': section.body[:100].replace('\n', ' '),
            })

    print(f"✓ {OUTPUT_CSV} を生成しました")


def print_header(title: str):
    """セクションヘッダを表示"""
    print("─" * 80)
    print(f"■ {title}")
    print("─" * 80)


def report_issues(issues: list[IssueReport], check_type: str, title: str):
    """チェック結果を報告"""
    filtered = [i for i in issues if i.check_type == check_type]

    if not filtered:
        print(f"\n[{check_type}] チェックパス\n")
        return

    print(f"\n[{check_type} {title}]")
    for issue in filtered:
        status_symbol = "NG" if issue.level == "NG" else "WARN" if issue.level == "WARN" else "?"
        if issue.file:
            print(f"  {status_symbol}  {issue.file}:{issue.line}  {issue.heading}")
            print(f"      {issue.message}")
        else:
            print(f"  {status_symbol}  {issue.message}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Fireball トレーサビリティ監査スクリプト"
    )
    parser.add_argument('--llm', action='store_true',
                        help='LLM意味整合チェック (L1) を実行')
    parser.add_argument('--model', type=str, default=None,
                        help='LLMモデルを指定')
    parser.add_argument('--verbose', action='store_true',
                        help='S1 マッピング詳細を表示')
    parser.add_argument('--debug', action='store_true',
                        help='デバッグログを表示')

    args = parser.parse_args()

    # ログファイル初期化
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = TMP_DIR / f"traceability_{now}.txt"
    log_file.parent.mkdir(exist_ok=True)

    with Tee(log_file) as tee:
        sys.stdout = tee

        print("\n" + "=" * 80)
        print("  Fireball コンポーネント仕様 × 要求キーワード トレーサビリティ監査")
        print("=" * 80 + "\n")

        # 1. 要求キーワードを読み込み
        print("[1] 要求キーワードを読み込み中...")
        requirements = load_requirement_keywords()
        print(f"    → {len(requirements)} 個のキーワードを検出\n")

        # 2. コンポーネント仕様をパース
        print("[2] コンポーネント仕様をパース中...")
        sections = load_all_sections()
        print(f"    → {len(sections)} 個のセクションを検出\n")

        # S1: マッピング詳細（verbose）
        if args.verbose:
            print_header("S1: セクション×キーワード マッピング")
            for section in sections:
                if section.keywords:
                    print(f"{section.file.relative_to(PROJECT_ROOT)}:{section.line_start} "
                          f"[{section.heading}]")
                    print(f"  → {section.keywords}\n")

        # 3. 機械的チェック
        print_header("機械的チェック (S2/S3)")

        print("[3] S2 チェック実行中: 出所不明セクション検出...")
        s2_issues = check_s2_orphan_sections(sections)
        report_issues(s2_issues, "S2", "出所不明セクション（キーワードなし）")

        print("[4] S3 チェック実行中: 要求漏れ検出...")
        s3_issues = check_s3_uncovered_requirements(requirements, sections)
        report_issues(s3_issues, "S3", "仕様未紐付き要求キーワード")

        # 4. LLM チェック（オプション）
        if args.llm:
            print_header("LLM意味整合チェック (L1)")
            print("[5] L1 チェック実行中: セクション内容と要求の整合性検証...")
            l1_issues = check_l1_semantic_alignment(
                sections, requirements, args.model, args.debug
            )
            report_issues(l1_issues, "L1", "セクション内容と要求の意味的整合性")

            # L1 結果を別CSV出力
            with open(
                COMPONENTS_DIR / f"traceability_llm_{now}.csv",
                'w', newline='', encoding='utf-8'
            ) as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'file', 'heading', 'keywords', 'llm_result', 'detail'
                ])
                writer.writeheader()
                for issue in l1_issues:
                    writer.writerow({
                        'file': str(issue.file) if issue.file else '',
                        'heading': issue.heading or '',
                        'keywords': issue.message,
                        'llm_result': issue.level,
                        'detail': issue.detail,
                    })

        # 5. CSV 出力
        print_header("出力ファイル生成")
        write_csv_matrix(sections, requirements)

        # サマリー
        print_header("サマリー")
        s2_count = len([i for i in s2_issues if i.level == "NG"])
        s3_count = len([i for i in s3_issues if i.level == "WARN"])
        print(f"S2 (出所不明セクション):        {s2_count} 件")
        print(f"S3 (要求漏れキーワード):        {s3_count} 件")
        if args.llm:
            l1_fail = len([i for i in l1_issues if i.level == "FAIL"])
            print(f"L1 (意味的不整合):             {l1_fail} 件\n")
        else:
            print()

        print(f"ログファイル: {log_file}\n")

    # stdout をリセット
    sys.stdout = sys.__stdout__
    print(f"✓ 監査完了。ログ: {log_file}")


if __name__ == "__main__":
    main()
