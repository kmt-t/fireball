#!/usr/bin/env python3
"""
Fireball 仕様整合性チェッカー

機械的チェック (F/T/A グループ) と LLM意味チェック (CSV駆動) を組み合わせて
コンポーネント仕様書間の整合性を検証する。

各フラグは独立して動作する。組み合わせても干渉しない。

使い方:
    python3 .claude/scripts/check_consistency.py [OPTIONS]

オプション:
    (なし)       機械的チェック F/T/A グループを実行
    --llm        consistency_checklist.csv を読み込んで LLM 整合性チェックを実行
    --gentable   spec_matrix.csv を機械生成し、続けて LLM で consistency_checklist.csv を生成
    --model M    LLM モデルを指定（--llm / --gentable に適用）
    --verbose    詳細ログを表示
    --debug      デバッグログ（LLM 生レスポンス等）を表示

LLM バックエンド（優先順位順）:
    1. SAKURA_AI_API_KEY が設定されていれば Sakura AI を使用
    2. OPEN_ROUTER_API_KEY が設定されていれば OpenRouter を使用
    3. どちらもなければ ollama を使用（ローカル）

生成ファイル:
    docs/components/spec_matrix.csv           コンポーネント × 要求キーワード 2D マトリクス
    docs/components/consistency_checklist.csv  LLM 生成チェックリスト（--gentable で生成、--llm で使用）

機械的チェック一覧:
  F: 記述規約 (FORMAT.md準拠)
    F1: `####` 見出しが C++ 識別子（バッククォート囲み）で始まっていないか
    F2: C++ コードブロック（```cpp 等）が使われていないか
    F3: 図が Mermaid 記法で書かれているか（非 Mermaid ツール / タグ漏れを検出）
  T: トレーサビリティ
    T1: コンポーネント仕様書が requirement_list.md に未定義のキーワードを参照していないか
    T2: requirement_list.md のキーワードがいずれかの仕様書から引用されているか（警告のみ）
    T3: requirement_list.md のキーワードがコンポーネント仕様書から引用されているか（警告のみ）
  A: アーキテクチャ整合性
    A1: Tier 1 公開 API が他の仕様書で表記ゆれ（camelCase / kebab-case）していないか

ハードコードなし設計:
    TEMPLATE_KW  : requirement_list.md の [Template & Meta] セクションから自動抽出
    Tier 型名    : Tier 1/2 仕様書のバッククォート識別子から機械的抽出
    API エイリアス: Tier 1 仕様書の公開 API 定義から snake_case → camelCase/kebab-case を自動生成
"""

import argparse
import csv
import json
import mistune
import os
import re
import sys
import textwrap
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = REPO_ROOT / "docs"
COMPONENTS_DIR = DOCS / "components"
REQUIREMENT_FILE = DOCS / "requires" / "requirement_list.md"
SPEC_MATRIX_CSV = COMPONENTS_DIR / "spec_matrix.csv"
CHECKLIST_CSV = COMPONENTS_DIR / "consistency_checklist.csv"

CHECKLIST_FIELDS = [
    'pair_id', 'file_a', 'file_b', 'shared_keywords',
    'file_a_section', 'file_b_section',
    'check_num', 'aspect', 'check_content',
    'llm_result', 'llm_reason',
]

KEYWORD_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")
TIER_PATTERN = re.compile(r"\*\*Tier (\d+)")

# 引数解析
parser = argparse.ArgumentParser(description="Fireball 仕様整合性チェッカー")
parser.add_argument("--llm", action="store_true", help="LLM 整合性チェックを実行")
parser.add_argument("--gentable", action="store_true", help="スペックマトリクスとチェックリストを生成")
parser.add_argument("--model", type=str, help="LLM モデルを指定")
parser.add_argument("--verbose", action="store_true", help="詳細ログを表示")
parser.add_argument("--debug", action="store_true", help="デバッグログを表示")
ARGS = parser.parse_args()

VERBOSE = ARGS.verbose
DEBUG = ARGS.debug
USE_LLM = ARGS.llm
GEN_TABLE = ARGS.gentable

def _read_api_key(name: str) -> str:
    """環境変数からAPIキーを読み込む。非ASCII文字が含まれる場合はパスキー誤りとしてエラー終了する。"""
    raw = os.environ.get(name, "")
    if not raw:
        return ""
    try:
        raw.encode("ascii")
    except UnicodeEncodeError:
        print(f"ERROR: {name} に非ASCII文字が含まれています。パスキーが間違っている可能性があります。", file=sys.stderr)
        sys.exit(1)
    return raw.strip()

SAKURA_AI_API_KEY = _read_api_key("SAKURA_AI_API_KEY")
OPEN_ROUTER_API_KEY = _read_api_key("OPEN_ROUTER_API_KEY")
GEMINI_API_KEY = _read_api_key("GEMINI_API_KEY") or _read_api_key("GOOGLE_API_KEY")

USE_SAKURA = bool(SAKURA_AI_API_KEY)
USE_OPENROUTER = bool(OPEN_ROUTER_API_KEY) and not USE_SAKURA
USE_GEMINI = bool(GEMINI_API_KEY) and not USE_SAKURA and not USE_OPENROUTER

SAKURA_MODEL = "gpt-oss-120b"
OPEN_ROUTER_MODEL = "google/gemini-3.1-pro-preview"
GEMINI_MODEL = "gemini-2.5-flash"
CHECK_MODEL = "qwen2.5-coder:3b"

if ARGS.model:
    SAKURA_MODEL = OPEN_ROUTER_MODEL = GEMINI_MODEL = ARGS.model

OLLAMA_URL = "http://localhost:11434/api/generate"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SAKURA_URL = "https://api.ai.sakura.ad.jp/v1/chat/completions"
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

RESET = "\033[0m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# ログ出力
# ---------------------------------------------------------------------------
class Tee:
    """stdout に出力しつつ、ANSIコードを除去したテキストをファイルにも書く。"""

    def __init__(self, file_path: Path) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = file_path.open("w", encoding="utf-8")
        self._stdout = sys.stdout
        sys.stdout = self

    def write(self, data: str) -> None:
        self._stdout.write(data)
        self._file.write(ANSI_ESCAPE.sub("", data))

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()

    def close(self) -> Path:
        sys.stdout = self._stdout
        self._file.close()
        return Path(self._file.name)


def log(msg: str) -> None:
    if VERBOSE or DEBUG:
        print(f"{DIM}{msg}{RESET}")


def debug(msg: str) -> None:
    if DEBUG:
        print(f"{DIM}[DEBUG] {msg}{RESET}")


# ---------------------------------------------------------------------------
# ファイル収集
# ---------------------------------------------------------------------------
_COMPONENT_SKIP = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}


def collect_all_md_files() -> list[Path]:
    return list(DOCS.rglob("*.md"))


def collect_component_md_files() -> list[Path]:
    return [p for p in COMPONENTS_DIR.rglob("*.md") if p.name not in _COMPONENT_SKIP]


# ---------------------------------------------------------------------------
# Markdown パーサーユーティリティ (mistune 3.x)
# ---------------------------------------------------------------------------
_md_parser = mistune.create_markdown(renderer=None)


def _parse_md_tokens(text: str) -> list[dict]:
    return _md_parser(text) or []


def _heading_text(token: dict) -> str:
    """heading トークンの children からプレーンテキストを結合する。"""
    return "".join(child.get("raw", "") for child in token.get("children", []))


def _token_text(token: dict) -> str:
    """ブロックトークンから本文テキストを再帰的に結合する。"""
    tp = token.get("type", "")
    if tp == "blank_line":
        return ""
    if tp == "block_code":
        info = token.get("attrs", {}).get("info", "")
        return f"```{info}\n{token.get('raw', '')}\n```"
    children = token.get("children", [])
    if children:
        return "".join(_token_text(c) for c in children)
    return token.get("raw", "")


# ---------------------------------------------------------------------------
# セクション抽出ユーティリティ
# ---------------------------------------------------------------------------
def extract_sections(text: str, headers: list[str], max_chars: int = 2000) -> str:
    """指定ヘッダを含む h1〜h3 セクションを抽出して結合する（文字数上限あり）。"""
    tokens = _parse_md_tokens(text)
    result: list[str] = []
    capturing = False
    current: list[str] = []

    for token in tokens:
        if token.get("type") == "heading":
            level = token.get("attrs", {}).get("level", 0)
            if level <= 3:
                if capturing and current:
                    result.append("\n".join(filter(None, current)))
                h_text = _heading_text(token)
                capturing = any(h.lower() in h_text.lower() for h in headers)
                current = [f"{'#' * level} {h_text}"] if capturing else []
                continue
        if capturing:
            chunk = _token_text(token)
            if chunk:
                current.append(chunk)

    if capturing and current:
        result.append("\n".join(filter(None, current)))

    combined = "\n\n".join(result)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n...(省略)..."
    return combined or "(対象セクションが見つかりませんでした)"


def call_llm(prompt: str, max_tokens: int = 768, openrouter: bool | None = None) -> str:
    """LLMにプロンプトを送り、レスポンス文字列を返す。"""
    if USE_SAKURA:
        return call_sakura(prompt, max_tokens)
    if USE_OPENROUTER:
        return call_openrouter(prompt, max_tokens)
    if USE_GEMINI:
        return call_gemini(prompt)
    return call_ollama(prompt, max_tokens)


def call_sakura(prompt: str, max_tokens: int = 768) -> str:
    payload = {
        "model": SAKURA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "service_tier": "flex",
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SAKURA_AI_API_KEY}",
    }
    debug(f"[Sakura] POST {SAKURA_URL}")
    debug(f"[Sakura] model={SAKURA_MODEL}, payload={len(data)} bytes")
    debug(f"[Sakura] request:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")

    req = urllib.request.Request(SAKURA_URL, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw_body = resp.read()
            debug(f"[Sakura] response: {raw_body.decode('utf-8', errors='replace')}")
            body = json.loads(raw_body)
            choices = body.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
            return json.dumps({"error": "No choices", "full_response": body})
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        debug(f"[Sakura] HTTP {e.code} {e.reason}")
        debug(f"[Sakura] ERROR BODY: {error_body}")
        try:
            return json.dumps({"error": f"HTTP {e.code}", "detail": json.loads(error_body)})
        except json.JSONDecodeError:
            return json.dumps({"error": f"HTTP {e.code}", "body": error_body})
    except urllib.error.URLError as e:
        debug(f"[Sakura] 接続エラー: {e.reason}")
        return json.dumps({"error": f"URLError: {e.reason}"})
    except Exception as e:
        debug(f"[Sakura] エラー: {type(e).__name__}: {e}")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def call_ollama(prompt: str, max_tokens: int = 768) -> str:
    payload = {
        "model": CHECK_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": max_tokens},
    }
    data = json.dumps(payload).encode("utf-8")
    debug(f"[Ollama] POST {OLLAMA_URL}")
    debug(f"[Ollama] model={CHECK_MODEL}, payload={len(data)} bytes")
    req = urllib.request.Request(OLLAMA_URL, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw_body = resp.read()
            debug(f"[Ollama] response: {raw_body.decode('utf-8', errors='replace')}")
            body = json.loads(raw_body)
            return body.get("response", "").strip()
    except Exception as e:
        debug(f"[Ollama] エラー: {type(e).__name__}: {e}")
        return f'{{"error": "{e}"}}'


def call_openrouter(prompt: str, max_tokens: int = 768) -> str:
    payload = {
        "model": OPEN_ROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "service_tier": "flex",
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPEN_ROUTER_API_KEY}",
    }

    debug(f"[OpenRouter] POST {OPENROUTER_URL}")
    debug(f"[OpenRouter] model={OPEN_ROUTER_MODEL}, payload={len(data)} bytes")
    debug(f"[OpenRouter] request:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")

    req = urllib.request.Request(OPENROUTER_URL, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw_body = resp.read()
            debug(f"[OpenRouter] response JSON: {raw_body.decode('utf-8', errors='replace')}")
            body = json.loads(raw_body)
            choices = body.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
            return json.dumps({"error": "No choices", "full_response": body})
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors='replace')
        debug(f"[OpenRouter] HTTP {e.code} {e.reason}")
        debug(f"[OpenRouter] ERROR BODY: {error_body}")
        try:
            return json.dumps({"error": f"HTTP {e.code}", "detail": json.loads(error_body)})
        except json.JSONDecodeError:
            return json.dumps({"error": f"HTTP {e.code}", "body": error_body})
    except urllib.error.URLError as e:
        debug(f"[OpenRouter] 接続エラー: {e.reason}")
        return json.dumps({"error": f"URLError: {e.reason}"})
    except Exception as e:
        debug(f"[OpenRouter] エラー: {type(e).__name__}: {e}")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def call_gemini(prompt: str) -> str:
    url = GEMINI_URL_TEMPLATE.format(model=GEMINI_MODEL, key=GEMINI_API_KEY)
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0
        },
        "service_tier": "flex"
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    debug(f"[Gemini] POST {url.split('?')[0]}")
    debug(f"[Gemini] model={GEMINI_MODEL}, payload={len(data)} bytes")
    debug(f"[Gemini] request:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")

    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw_body = resp.read()
            debug(f"[Gemini] response JSON: {raw_body.decode('utf-8', errors='replace')}")
            body = json.loads(raw_body)
            candidates = body.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
            return json.dumps({"error": "No candidates", "full_response": body})
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors='replace')
        debug(f"[Gemini] HTTP {e.code} {e.reason}")
        debug(f"[Gemini] ERROR BODY: {error_body}")
        try:
            return json.dumps({"error": f"HTTP {e.code}", "detail": json.loads(error_body)})
        except json.JSONDecodeError:
            return json.dumps({"error": f"HTTP {e.code}", "body": error_body})
    except urllib.error.URLError as e:
        debug(f"[Gemini] 接続エラー: {e.reason}")
        return json.dumps({"error": f"URLError: {e.reason}"})
    except Exception as e:
        debug(f"[Gemini] エラー: {type(e).__name__}: {e}")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def parse_llm_json(raw: str) -> dict:
    """LLMのレスポンスからJSONを抽出してパースする。"""
    # コードフェンス行を除去（LLMが ```json ... ``` で返すことがある）
    cleaned = "\n".join(
        ln for ln in raw.splitlines() if not ln.strip().startswith("```")
    ).strip()

    decoder = json.JSONDecoder()
    for i, ch in enumerate(cleaned):
        if ch in ('{', '['):
            try:
                obj, _ = decoder.raw_decode(cleaned, i)
                if isinstance(obj, list):
                    return {"items": obj}
                return obj
            except json.JSONDecodeError:
                continue

    return {"status": "ERROR", "reason": "JSONを解析できませんでした", "raw": raw[:200]}


_LLM_CHECK_PREAMBLE = textwrap.dedent("""\
    あなたはFireballプロジェクトの仕様書整合性チェッカーです。
    2つの仕様書の抜粋を比較し、指定された観点で整合性を判定してください。

    【出力ルール】
    - 以下のJSON形式のみで回答すること。説明文・前置き・コードブロックは不要。
    - statusは PASS（整合）, FAIL（矛盾あり）, WARN（記述不足/判断不能）のいずれか。

    出力例:
    {"items":[{"id":"1","status":"PASS","reason":"両方の文書で同じ値を使用"},{"id":"2","status":"FAIL","reason":"Aでは5KB、Bでは8KBと記述が異なる"}],"summary":"FAIL"}

""")


def llm_check(pair_id: str, label: str, excerpt_a: str, excerpt_b: str,
              items: list[tuple[str, str]]) -> dict:
    """LLMで整合性チェックを実施し、結果のdictを返す。"""
    items_text = "\n".join(f"- [{item_id}] {desc}" for item_id, desc in items)

    prompt = _LLM_CHECK_PREAMBLE + textwrap.dedent(f"""\
        ## チェック対象: {pair_id} - {label}

        ### 仕様書 A の抜粋
        {excerpt_a}

        ### 仕様書 B の抜粋
        {excerpt_b}

        ### チェック項目（各項目を判定してください）
        {items_text}

        上記を根拠として、各チェック項目のstatusとreasonを含むJSONのみを出力してください。
    """)

    log(f"  [LLM] {pair_id} にプロンプト送信中 (model={CHECK_MODEL})...")
    raw = call_llm(prompt, max_tokens=2048)
    log(f"  [LLM] 生レスポンス ({len(raw)} chars): {raw[:500]}")
    result = parse_llm_json(raw)
    if "error" in result or result.get("status") == "ERROR":
        print(f"  {YELLOW}[LLM] パース失敗。生レスポンス:{RESET}")
        print(f"  {YELLOW}{raw}{RESET}")
    return result


# ---------------------------------------------------------------------------
# F: 記述規約 (FORMAT.md準拠)
# ---------------------------------------------------------------------------
_CPP_IDENT_HEADING = re.compile(r"^####\s+`[a-zA-Z_][a-zA-Z0-9_]*`")


def check_f1(component_files: list[Path]) -> list[tuple[Path, int, str]]:
    """
    `####` 見出しがC++識別子（バッククォート囲み）で始まっていないか確認する。
    FORMAT.md の規約: 見出しは自然言語、識別子は補足として括弧内に添える。
    Returns: [(path, line_number, heading_text), ...]
    """
    violations = []
    for path in component_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CPP_IDENT_HEADING.match(line):
                violations.append((path, lineno, line.strip()))
    return violations


_CPP_FENCE = re.compile(r"^```(cpp|c\+\+|cxx|c)$", re.IGNORECASE)


def check_f2(component_files: list[Path]) -> list[tuple[Path, int, str]]:
    """
    C++ コードブロック（```cpp 等）が使われていないか確認する。
    FORMAT.md の規約: 擬似コード・サンプルコードは Python で記述する。
    Returns: [(path, line_number, fence_text), ...]
    """
    violations = []
    for path in component_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CPP_FENCE.match(line.strip()):
                violations.append((path, lineno, line.strip()))
    return violations


_NON_MERMAID_DIAGRAM_FENCE = re.compile(
    r"^(plantuml|uml|graphviz|dot|ditaa|blockdiag|nwdiag|seqdiag|actdiag)$",
    re.IGNORECASE,
)
_MERMAID_KEYWORD = re.compile(
    r"^(graph |sequenceDiagram|stateDiagram|classDiagram|flowchart |gantt|"
    r"gitGraph|pie |erDiagram|journey|timeline|mindmap|block-beta|architecture-beta)"
)


def check_f3(component_files: list[Path]) -> list[tuple[Path, int, str]]:
    """
    図がMermaid記法で書かれているかを確認する。
    - Mermaid以外のダイアグラムツール（plantuml等）のコードブロックを検出
    - Mermaidキーワードを含むのに ```mermaid タグが付いていないブロックを検出
    Returns: [(path, line_number, description), ...]
    """
    violations = []
    for path in component_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        in_fence = False
        fence_lang = ""
        fence_body: list[str] = []
        fence_start = 0
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("```") and not in_fence:
                in_fence = True
                fence_lang = stripped[3:].strip()
                fence_body = []
                fence_start = lineno
            elif stripped.startswith("```") and in_fence:
                in_fence = False
                if _NON_MERMAID_DIAGRAM_FENCE.match(fence_lang):
                    violations.append((
                        path, fence_start,
                        f"非Mermaidダイアグラムツール: ```{fence_lang}",
                    ))
                elif fence_lang != "mermaid":
                    body = "\n".join(fence_body)
                    if _MERMAID_KEYWORD.search(body):
                        violations.append((
                            path, fence_start,
                            f"Mermaid内容に ```mermaid タグなし (```{fence_lang or '(なし)'})",
                        ))
                fence_body = []
                fence_lang = ""
            elif in_fence:
                fence_body.append(line)
    return violations


# ---------------------------------------------------------------------------
# T: トレーサビリティ
# ---------------------------------------------------------------------------
def _load_template_keywords() -> set[str]:
    """requirement_list.md の [Template & Meta] セクションからプレースホルダーキーワードを抽出する。"""
    if not REQUIREMENT_FILE.exists():
        return set()
    text = REQUIREMENT_FILE.read_text(encoding="utf-8")
    tokens = _parse_md_tokens(text)
    in_section = False
    result: set[str] = set()
    for token in tokens:
        if token.get("type") == "heading":
            in_section = "Template & Meta" in _heading_text(token)
        elif in_section:
            result |= set(KEYWORD_PATTERN.findall(_token_text(token)))
    return result


TEMPLATE_KW: set[str] = _load_template_keywords()


def extract_keywords(text: str) -> set[str]:
    return set(KEYWORD_PATTERN.findall(text))


def load_defined_keywords() -> set[str]:
    kws = set()
    if REQUIREMENT_FILE.exists():
        kws |= extract_keywords(REQUIREMENT_FILE.read_text(encoding="utf-8"))
    doc_struct = DOCS / "architecture" / "document_structure.md"
    if doc_struct.exists():
        kws |= extract_keywords(doc_struct.read_text(encoding="utf-8"))
    return kws


def check_t1(defined: set[str], component_files: list[Path]) -> list[tuple[Path, str]]:
    errors = []
    for path in component_files:
        text = path.read_text(encoding="utf-8")
        for kw in extract_keywords(text) - defined - TEMPLATE_KW:
            errors.append((path, kw))
    return errors


def check_t2(defined: set[str], all_files: list[Path]) -> set[str]:
    referenced: set[str] = set()
    doc_struct = DOCS / "architecture" / "document_structure.md"
    for path in all_files:
        if path in (REQUIREMENT_FILE, doc_struct):
            continue
        referenced |= extract_keywords(path.read_text(encoding="utf-8"))
    return defined - referenced - TEMPLATE_KW


def check_t3(defined: set[str], component_files: list[Path]) -> set[str]:
    referenced: set[str] = set()
    for path in component_files:
        referenced |= extract_keywords(path.read_text(encoding="utf-8"))
    return defined - referenced - TEMPLATE_KW


# ---------------------------------------------------------------------------
# A: アーキテクチャ整合性
# ---------------------------------------------------------------------------


def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _snake_to_kebab(name: str) -> str:
    return name.replace("_", "-")


_SNAKE_FUNC = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def _load_api_aliases() -> tuple[dict[str, list[str]], set[str]]:
    """
    Tier 1 コンポーネント仕様書の公開 API セクション（#### `func_name`）から
    snake_case メソッド名を抽出し、camelCase / kebab-case のエイリアス辞書と
    除外ファイル名セット（定義元ファイル + requirements）を生成する。
    """
    aliases: dict[str, list[str]] = {}
    skip: set[str] = {"CONSISTENCY_MATRIX.md", REQUIREMENT_FILE.name}

    for path in COMPONENTS_DIR.rglob("*.md"):
        if path.name in _COMPONENT_SKIP:
            continue
        text = path.read_text(encoding="utf-8")
        tiers = TIER_PATTERN.findall(text)
        if not tiers or int(tiers[0]) != 1:
            continue

        skip.add(path.name)
        for token in _parse_md_tokens(text):
            if token.get("type") != "heading" or token.get("attrs", {}).get("level") != 4:
                continue
            for child in token.get("children", []):
                if child.get("type") != "codespan":
                    continue
                name = child.get("raw", "")
                if not _SNAKE_FUNC.match(name):
                    continue
                camel = _snake_to_camel(name)
                kebab = _snake_to_kebab(name)
                variants = [v for v in (camel, kebab) if v != name]
                if variants:
                    existing = aliases.setdefault(name, [])
                    for v in variants:
                        if v not in existing:
                            existing.append(v)

    return aliases, skip


def check_a1(all_files: list[Path]) -> list[tuple[Path, str, str]]:
    ipc_aliases, api_skip = _load_api_aliases()
    violations = []
    for path in all_files:
        if path.name in api_skip:
            continue
        text = path.read_text(encoding="utf-8")
        for canonical, alias_list in ipc_aliases.items():
            for alias in alias_list:
                if alias in text:
                    violations.append((path, canonical, alias))
    return violations


# ---------------------------------------------------------------------------
# スペックマトリクス CSV（コンポーネント × 要求キーワード 2Dマトリクス）
# ---------------------------------------------------------------------------
def generate_spec_matrix() -> tuple[list[str], list[str], dict[str, set[str]]]:
    """
    コンポーネントファイル × 要求キーワード の2Dマトリクスデータを生成する。
    """
    comp_files = sorted(
        p for p in COMPONENTS_DIR.rglob("*.md") if p.name not in _COMPONENT_SKIP
    )

    defined_kw = load_defined_keywords()

    file_kw_map: dict[str, set[str]] = {}
    for f in comp_files:
        text = f.read_text(encoding="utf-8")
        kws = (extract_keywords(text) & defined_kw) - TEMPLATE_KW
        rel = str(f.relative_to(REPO_ROOT))
        file_kw_map[rel] = kws

    all_kw = sorted(defined_kw - TEMPLATE_KW)
    all_files = sorted(file_kw_map.keys())

    return all_kw, all_files, file_kw_map


def save_spec_matrix_csv(all_kw, all_files, file_kw_map):
    SPEC_MATRIX_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SPEC_MATRIX_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["component"] + all_kw)
        for fp in all_files:
            kws = file_kw_map.get(fp, set())
            writer.writerow([fp] + ["1" if k in kws else "0" for k in all_kw])


def extract_keyword_definitions(req_text: str) -> dict[str, str]:
    """
    requirement_list.md のテーブル行からキーワード → 定義テキストのマッピングを抽出する。
    """
    definitions: dict[str, str] = {}
    pattern = re.compile(r'^\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|\s*([^|]+?)\s*\|')
    for line in req_text.splitlines():
        m = pattern.match(line)
        if m:
            definitions[m.group(1)] = m.group(2).strip()
    return definitions


def find_sections_with_keyword(text: str, keyword: str) -> list[str]:
    """テキスト内で `{keyword}` を含む段落が属する h1〜h3 セクション見出しを返す。

    extract_sections と整合させるため h4 以下の見出しは無視する。
    `####` 以下の小見出し内にキーワードがある場合は、その親 h1〜h3 セクションを返す。
    """
    tokens = _parse_md_tokens(text)
    current_header = "(ファイル先頭)"
    found: list[str] = []
    target = f"{{{keyword}}}"

    for token in tokens:
        if token.get("type") == "heading":
            level = token.get("attrs", {}).get("level", 0)
            if level <= 3:
                current_header = _heading_text(token)
        elif target in _token_text(token) and current_header not in found:
            found.append(current_header)

    return found


def build_keyword_section_map(
    all_files: list[str],
    file_kw_map: dict[str, set[str]],
) -> dict[str, list[dict]]:
    """キーワードごとに言及しているファイルとセクションヘッダを集める。"""
    kw_map: dict[str, list[dict]] = {}
    for fp in all_files:
        file_path = REPO_ROOT / fp
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8")
        for kw in file_kw_map.get(fp, set()):
            headers = find_sections_with_keyword(text, kw)
            if not headers:
                continue
            if kw not in kw_map:
                kw_map[kw] = []
            kw_map[kw].append({"file": fp, "sections": headers})
    return {kw: mentions for kw, mentions in kw_map.items() if len(mentions) >= 2}


def generate_checklist_from_matrix(
    all_files: list[str],
    file_kw_map: dict[str, set[str]],
) -> list[dict]:
    checklist_path = COMPONENTS_DIR / "CHECKLIST.md"
    matrix_path = COMPONENTS_DIR / "CONSISTENCY_MATRIX.md"

    checklist_text = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else ""
    aspect_text = ""
    if matrix_path.exists():
        aspect_text = extract_sections(
            matrix_path.read_text(encoding="utf-8"), ["観点"], max_chars=1200
        )

    kw_definitions = {}
    if REQUIREMENT_FILE.exists():
        kw_definitions.update(extract_keyword_definitions(REQUIREMENT_FILE.read_text(encoding="utf-8")))
    doc_struct = DOCS / "architecture" / "document_structure.md"
    if doc_struct.exists():
        kw_definitions.update(extract_keyword_definitions(doc_struct.read_text(encoding="utf-8")))

    kw_section_map = build_keyword_section_map(all_files, file_kw_map)
    name_to_path: dict[str, str] = {Path(fp).name: fp for fp in all_files}

    MAX_KEYWORDS = 35
    sorted_kws = sorted(
        kw_section_map.items(), key=lambda x: len(x[1]), reverse=True
    )[:MAX_KEYWORDS]

    kw_data = [
        {
            "keyword": kw,
            "definition": kw_definitions.get(kw, ""),
            "mentions": [
                {"file": m["file"], "sections": m["sections"][:3]}
                for m in mentions
            ],
        }
        for kw, mentions in sorted_kws
    ]

    prompt = textwrap.dedent(f"""\
        あなたはFireballプロジェクトの仕様書整合性チェッカーです。
        以下の「キーワード×ドキュメントセクション情報」を基に、CHECKLIST.md の観点から
        整合性チェックが必要なコンポーネントペアとチェック項目を生成してください。

        ## 観点コード（A〜I）
        {aspect_text}

        ## セルフチェック観点（CHECKLIST.md 抜粋）
        {checklist_text[:1500]}

        ## キーワード×ドキュメントセクション一覧
        {json.dumps(kw_data, ensure_ascii=False, indent=2)}

        ## 出力ルール
        - 以下のJSON形式のみで出力すること。
        - file_a_section/file_b_section は "sections" 内のヘッダ文字列をそのまま使用すること。
        - check_content は具体的記述。
        - aspect は A〜I のコード。
        - 1ペアにつき1〜4個のチェック項目を生成。

        {{"pairs":[{{"file_a":"path/a.md","file_b":"path/b.md","shared_keywords":["kw1"],"checks":[{{"aspect":"A","file_a_section":"## 3.1 ...","file_b_section":"## 4.1 ...","check_content":"チェック内容"}}]}}]}}
    """)

    backend, model_name = get_model_info()
    backend, model_name = get_model_info()
    log(f"  [LLM] チェックリスト生成中... (バックエンド: {backend}, モデル: {model_name})")
    raw = call_llm(prompt, max_tokens=8192)
    result = parse_llm_json(raw)

    if "error" in result or result.get("status") == "ERROR":
        print(f"  [LLM] チェックリスト生成失敗: {result.get('error', result)}")
        return []

    items: list[dict] = []
    for pair_idx, pair_data in enumerate(result.get("pairs", [])):
        fa = pair_data.get("file_a", "")
        fb = pair_data.get("file_b", "")
        fa = name_to_path.get(fa, name_to_path.get(Path(fa).name, fa))
        fb = name_to_path.get(fb, name_to_path.get(Path(fb).name, fb))
        pair_id = f"G{pair_idx + 1:02d}"

        for check_idx, check in enumerate(pair_data.get("checks", []), start=1):
            items.append({
                "pair_id": pair_id,
                "file_a": fa,
                "file_b": fb,
                "shared_keywords": ",".join(pair_data.get("shared_keywords", [])),
                "file_a_section": check.get("file_a_section", ""),
                "file_b_section": check.get("file_b_section", ""),
                "check_num": str(check_idx),
                "aspect": check.get("aspect", ""),
                "check_content": check.get("check_content", ""),
                "llm_result": "",
                "llm_reason": "",
            })
    return items


def save_csv_checklist(items: list[dict]) -> None:
    CHECKLIST_CSV.parent.mkdir(parents=True, exist_ok=True)
    with CHECKLIST_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CHECKLIST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)


def read_csv_checklist() -> list[dict]:
    if not CHECKLIST_CSV.exists():
        return []
    with CHECKLIST_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ---------------------------------------------------------------------------
# LLM整合性チェックエンジン
# ---------------------------------------------------------------------------
def check_pair_llm(pair_id: str, pair_items: list[dict]) -> dict:
    file_a_rel = pair_items[0].get("file_a", "")
    file_b_rel = pair_items[0].get("file_b", "")

    file_a_path, file_b_path = REPO_ROOT / file_a_rel, REPO_ROOT / file_b_rel

    if not file_a_path.exists() or not file_b_path.exists():
        return {"summary": "ERROR", "items": [], "error": f"not found: {file_a_rel} or {file_b_rel}"}

    text_a, text_b = file_a_path.read_text(encoding="utf-8"), file_b_path.read_text(encoding="utf-8")

    def _get_hints(items: list[dict], key: str) -> list[str]:
        hints = []
        for item in items:
            h = item.get(key)
            if h:
                clean = h.lstrip("#").strip()
                if clean:
                    hints.append(clean)
        return list(dict.fromkeys(hints))

    a_hints, b_hints = _get_hints(pair_items, "file_a_section"), _get_hints(pair_items, "file_b_section")
    excerpt_a = extract_sections(text_a, a_hints or a_hints, max_chars=3000)
    excerpt_b = extract_sections(text_b, b_hints or b_hints, max_chars=3000)

    label = f"{Path(file_a_rel).name} × {Path(file_b_rel).name}"
    return llm_check(pair_id, label, excerpt_a, excerpt_b, [(i["check_num"], i["check_content"]) for i in pair_items])


def run_llm_checks_from_items(items: list[dict]) -> int:
    pairs: dict[str, list[dict]] = {}
    for item in items:
        pairs.setdefault(item["pair_id"], []).append(item)

    total_errors = 0
    for pid, pitems in pairs.items():
        result = check_pair_llm(pid, pitems)
        label = f"{Path(pitems[0]['file_a']).name} × {Path(pitems[0]['file_b']).name}"
        total_errors += report_llm(pid, label, result)
        rmap = {r.get("id", ""): r for r in result.get("items", [])}
        for item in pitems:
            r = rmap.get(item["check_num"])
            if r:
                item["llm_result"], item["llm_reason"] = r.get("status", ""), r.get("reason", "")
    return total_errors


# ---------------------------------------------------------------------------
# レポート出力
# ---------------------------------------------------------------------------
STATUS_COLOR = {"PASS": GREEN, "FAIL": RED, "WARN": YELLOW, "ERROR": RED}

def report_mechanical(title: str, items: list, formatter, warn: bool = False) -> int:
    color, label = (YELLOW, "WARN") if warn else (RED, "NG")
    print(f"\n{BOLD}{CYAN}[{title}]{RESET}")
    if not items:
        print(f"  {GREEN}OK — 問題なし{RESET}"); return 0
    for item in items:
        print(f"  {color}{label}{RESET} {formatter(item)}")
    return len(items)


def report_llm(pair_id: str, label: str, result: dict) -> int:
    summary = result.get("summary", "ERROR")
    print(f"\n{BOLD}{CYAN}[{pair_id}: {label}]{RESET}  →  {STATUS_COLOR.get(summary, RED)}{BOLD}{summary}{RESET}")
    for item in result.get("items", []):
        s = item.get("status", "?")
        print(f"  [{item.get('id','?')}] {STATUS_COLOR.get(s, RESET)}{s}{RESET}  {item.get('reason','')}")
    return 0 if summary == "PASS" else 1


def get_model_info():
    if USE_SAKURA: return "Sakura AI", SAKURA_MODEL
    if USE_OPENROUTER: return "OpenRouter", OPEN_ROUTER_MODEL
    return "ollama", CHECK_MODEL


def run_mechanical_checks():
    defined_kw = load_defined_keywords()
    comp_files, all_files = collect_component_md_files(), collect_all_md_files()
    total_errors = 0
    print(f"\n{'─'*60}\n{BOLD}■ F: 記述規約{RESET}\n{'─'*60}")
    total_errors += report_mechanical("F1 C++識別子の見出し使用", check_f1(comp_files), lambda e: f"{e[0].relative_to(REPO_ROOT)}:{e[1]}  →  {e[2]}")
    total_errors += report_mechanical("F2 C++コードブロック使用", check_f2(comp_files), lambda e: f"{e[0].relative_to(REPO_ROOT)}:{e[1]}  →  {e[2]}")
    total_errors += report_mechanical("F3 図のMermaid非準拠", check_f3(comp_files), lambda e: f"{e[0].relative_to(REPO_ROOT)}:{e[1]}  →  {e[2]}")

    print(f"\n{'─'*60}\n{BOLD}■ T: トレーサビリティ{RESET}\n{'─'*60}")
    total_errors += report_mechanical("T1 未定義キーワード参照", check_t1(defined_kw, comp_files), lambda e: f"{e[0].relative_to(REPO_ROOT)}  →  {{{e[1]}}} が未定義")
    report_mechanical("T2 孤立キーワード", sorted(check_t2(defined_kw, all_files)), lambda kw: f"{{{kw}}} は引用なし", warn=True)
    report_mechanical("T3 コンポーネント未カバーキーワード", sorted(check_t3(defined_kw, comp_files)), lambda kw: f"{{{kw}}} はコンポーネント仕様書で未引用", warn=True)

    print(f"\n{'─'*60}\n{BOLD}■ A: アーキテクチャ整合性{RESET}\n{'─'*60}")
    total_errors += report_mechanical("A1 API名の表記ゆれ", check_a1(all_files), lambda e: f"{e[0].relative_to(REPO_ROOT)}  →  '{e[2]}' (正式名: {e[1]})")

    return total_errors


def perform_gentable():
    print(f"\n{BOLD}■ スペックマトリクス生成(--gentable){RESET}")
    all_kw, all_files, file_kw_map = generate_spec_matrix()
    save_spec_matrix_csv(all_kw, all_files, file_kw_map)
    print(f"  保存完了: {SPEC_MATRIX_CSV.relative_to(REPO_ROOT)}")

    checklist_items = generate_checklist_from_matrix(all_files, file_kw_map)
    if checklist_items:
        save_csv_checklist(checklist_items)
        print(f"  生成完了: {CHECKLIST_CSV.relative_to(REPO_ROOT)} ({len(checklist_items)} 項目)")


def run_llm_checks():
    items = read_csv_checklist()
    if not items:
        print(f"  {YELLOW}警告: チェックリストCSVが見つかりません{RESET}")
        return 1

    backend, model_name = get_model_info()
    print(f"\n{BOLD}■ LLM整合性チェック (バックエンド: {backend}, モデル: {model_name}){RESET}")
    print(f"  {len(items)} 件の項目をチェック中...")

    errs = run_llm_checks_from_items(items)
    save_csv_checklist(items)
    return errs


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPO_ROOT / "tmp" / f"consistency_{timestamp}.txt"
    tee = Tee(out_path)

    print(f"{BOLD}Fireball 仕様整合性チェッカー{RESET}")
    print(f"結果ファイル: {out_path.relative_to(REPO_ROOT)}")

    # フラグの確認
    has_action = ARGS.llm or ARGS.gentable

    if ARGS.gentable:
        perform_gentable()

    if ARGS.llm:
        run_llm_checks()

    if not has_action:
        # デフォルト: 機械的チェックのみ
        run_mechanical_checks()

    print(f"\n{'='*60}")
    print(f"処理完了: {tee.close()}", file=sys.stdout)
    sys.exit(0)



if __name__ == "__main__":
    main()
