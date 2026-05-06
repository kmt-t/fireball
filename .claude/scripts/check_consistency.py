#!/usr/bin/env python3
"""
Fireball 仕様整合性チェッカー

機械的チェック (X1-X5) と LLM意味チェック (CSV駆動) を組み合わせて
コンポーネント仕様書間の整合性を検証する。

使い方:
    python3 .claude/scripts/check_consistency.py [--verbose] [--debug] [--llm] [--gentable] [--model MODEL]

オプション:
    --verbose    詳細ログを表示
    --debug      デバッグログを表示
    --llm        LLM による意味チェックを実行
    --gentable   スペックマトリクスCSVを生成し、--llm と組み合わせると
                 CHECKLIST.md の観点からチェックリストCSVも生成する
    --model M    使用するLLMモデル
               ANTHROPIC_API_KEY が設定されていれば Anthropic Claude を使用、
               OPEN_ROUTER_API_KEY が設定されていれば OpenRouter を使用、
               否ければ ollama を使用 (デフォルト: qwen2.5-coder:3b)

CSVファイル:
    docs/components/spec_matrix.csv        コンポーネント × 要求キーワード 2Dマトリクス
    docs/components/consistency_checklist.csv  LLM生成チェックリスト（ペア × 観点 × チェック項目）

実行パターン:
    --gentable のみ     : spec_matrix.csv を機械的に生成
    --llm --gentable   : spec_matrix.csv 生成 → LLM で consistency_checklist.csv を生成 → LLM チェック実行
    --llm のみ         : 既存の consistency_checklist.csv を使って LLM チェック実行
"""

import csv
import json
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
BUDGET_FILE = DOCS / "architecture" / "resource_budget.md"
ARCH_FILE = DOCS / "architecture" / "architecture_overview.md"

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

VERBOSE = "--verbose" in sys.argv
DEBUG = "--debug" in sys.argv
USE_LLM = "--llm" in sys.argv
GEN_TABLE = "--gentable" in sys.argv

# LLMバックエンドの決定
# --gentable (チェックリスト生成) → OpenRouter (Pro)
# --llm のみ (整合性チェック実行) → Ollama (ローカル)
OPEN_ROUTER_API_KEY = os.environ.get("OPEN_ROUTER_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

USE_ANTHROPIC = bool(ANTHROPIC_API_KEY)
USE_OPENROUTER = bool(OPEN_ROUTER_API_KEY) and not USE_ANTHROPIC

ANTHROPIC_MODEL = "claude-opus-4-7"
GENTABLE_MODEL = "google/gemini-3.1-pro-preview"  # --gentable 用 OpenRouter モデル
CHECK_MODEL = "qwen2.5-coder:3b"                   # --llm チェック用 Ollama モデル

for i, a in enumerate(sys.argv):
    if a == "--model" and i + 1 < len(sys.argv):
        GENTABLE_MODEL = CHECK_MODEL = sys.argv[i + 1]

# 後方互換のため MODEL は gentable モデルに合わせる
MODEL = GENTABLE_MODEL if USE_OPENROUTER else (ANTHROPIC_MODEL if USE_ANTHROPIC else CHECK_MODEL)

OLLAMA_URL = "http://localhost:11434/api/generate"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

RESET = "\033[0m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


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
def collect_all_md_files() -> list[Path]:
    return list(DOCS.rglob("*.md"))


def collect_component_md_files() -> list[Path]:
    skip = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}
    return [p for p in COMPONENTS_DIR.rglob("*.md") if p.name not in skip]


# ---------------------------------------------------------------------------
# セクション抽出ユーティリティ
# ---------------------------------------------------------------------------
def extract_sections(text: str, headers: list[str], max_chars: int = 2000) -> str:
    """指定ヘッダを含む段落を抽出して結合する（文字数上限あり）。"""
    lines = text.splitlines()
    result: list[str] = []
    capturing = False
    current: list[str] = []

    for line in lines:
        if re.match(r"^#{1,3} ", line):
            if capturing and current:
                result.append("\n".join(current))
            capturing = any(h.lower() in line.lower() for h in headers)
            current = [line] if capturing else []
        elif capturing:
            current.append(line)

    if capturing and current:
        result.append("\n".join(current))

    combined = "\n\n".join(result)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n...(省略)..."
    return combined or "(対象セクションが見つかりませんでした)"


def load_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# LLM API
# ---------------------------------------------------------------------------
def call_llm(prompt: str, max_tokens: int = 768, openrouter: bool | None = None) -> str:
    """LLMにプロンプトを送り、レスポンス文字列を返す。
    openrouter=True: OpenRouterを強制 (GENTABLE_MODEL使用)
    openrouter=False: Ollamaを強制 (CHECK_MODEL使用)
    openrouter=None: 環境変数で決定
    """
    if USE_ANTHROPIC:
        return call_anthropic(prompt, max_tokens)
    use_or = USE_OPENROUTER if openrouter is None else (openrouter and bool(OPEN_ROUTER_API_KEY))
    if use_or:
        return call_openrouter(prompt, max_tokens)
    return call_ollama(prompt, max_tokens)


def call_anthropic(prompt: str, max_tokens: int = 768) -> str:
    try:
        import anthropic
    except ImportError:
        return '{"error": "anthropic パッケージが未インストール (pip install anthropic)"}'

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return (message.content[0].text if message.content else "").strip()
    except Exception as e:
        return f'{{"error": "{type(e).__name__}: {e}"}}'


def call_ollama(prompt: str, max_tokens: int = 768) -> str:
    payload = {
        "model": CHECK_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": max_tokens},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.load(resp)
            return body.get("response", "").strip()
    except Exception as e:
        return f'{{"error": "{e}"}}'


def call_openrouter(prompt: str, max_tokens: int = 768) -> str:
    payload = {
        "model": GENTABLE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPEN_ROUTER_API_KEY}",
    }

    print(f"  {DIM}[OpenRouter] POST {OPENROUTER_URL}{RESET}")
    print(f"  {DIM}[OpenRouter] model={GENTABLE_MODEL}, payload={len(data)} bytes{RESET}")
    if DEBUG:
        debug(f"[OpenRouter] request:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")

    req = urllib.request.Request(OPENROUTER_URL, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw_body = resp.read()
            print(f"  {DIM}[OpenRouter] HTTP 200 OK, {len(raw_body)} bytes{RESET}")
            debug(f"[OpenRouter] response:\n{raw_body.decode('utf-8', errors='replace')}")
            body = json.loads(raw_body)
            choices = body.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
            print(f"  {YELLOW}[OpenRouter] WARNING: choices が空{RESET}")
            return json.dumps({"error": "No choices", "full_response": body})
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"  {RED}[OpenRouter] HTTP {e.code} {e.reason}{RESET}")
        print(f"  {RED}{error_body}{RESET}")
        try:
            return json.dumps({"error": f"HTTP {e.code}", "detail": json.loads(error_body)})
        except json.JSONDecodeError:
            return json.dumps({"error": f"HTTP {e.code}", "body": error_body})
    except urllib.error.URLError as e:
        print(f"  {RED}[OpenRouter] 接続エラー: {e.reason}{RESET}")
        return json.dumps({"error": f"URLError: {e.reason}"})
    except Exception as e:
        print(f"  {RED}[OpenRouter] エラー: {type(e).__name__}: {e}{RESET}")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def parse_llm_json(raw: str) -> dict:
    """LLMのレスポンスからJSONを抽出してパースする。"""
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"status": "ERROR", "reason": "JSONを解析できませんでした", "raw": raw[:200]}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        return {"status": "ERROR", "reason": str(e), "raw": raw[:200]}


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
# X1: 未定義キーワード参照
# ---------------------------------------------------------------------------
def extract_keywords(text: str) -> set[str]:
    return set(KEYWORD_PATTERN.findall(text))


def load_defined_keywords() -> set[str]:
    text = REQUIREMENT_FILE.read_text(encoding="utf-8")
    return extract_keywords(text)


TEMPLATE_KW = {"Decision_Key", "Strategy_Key", "Requirement_Key", "req_id", "concept"}


def check_x1(defined: set[str], component_files: list[Path]) -> list[tuple[Path, str]]:
    errors = []
    for path in component_files:
        text = path.read_text(encoding="utf-8")
        for kw in extract_keywords(text) - defined - TEMPLATE_KW:
            errors.append((path, kw))
    return errors


# ---------------------------------------------------------------------------
# X2: 孤立キーワード
# ---------------------------------------------------------------------------
def check_x2(defined: set[str], all_files: list[Path]) -> set[str]:
    referenced: set[str] = set()
    for path in all_files:
        if path == REQUIREMENT_FILE:
            continue
        referenced |= extract_keywords(path.read_text(encoding="utf-8"))
    return defined - referenced - TEMPLATE_KW


# ---------------------------------------------------------------------------
# X3: Tier規制違反
# ---------------------------------------------------------------------------
_IMPL_CTX = re.compile(
    r"(harness|include|依存注入|ConceptHarnessDI|static_cast|reinterpret_cast|via harness)",
    re.IGNORECASE,
)
_TIER1_TYPES = {"ipc_router_harness", "ipc_router_t", "IpcRouter", "ipc_router&", "ipc_router*"}
_TIER2_TYPES = {"coos_harness", "coos_context", "vsoc_harness", "vsoc_context"}


def check_x3(component_files: list[Path]) -> list[tuple[Path, str, str]]:
    violations = []
    for path in component_files:
        text = path.read_text(encoding="utf-8")
        tiers = TIER_PATTERN.findall(text)
        if not tiers or int(tiers[0]) != 3:
            continue
        for line in text.splitlines():
            for t in _TIER1_TYPES | _TIER2_TYPES:
                if t in line and _IMPL_CTX.search(line):
                    violations.append((path, "Tier 3", t))
    return violations


# ---------------------------------------------------------------------------
# X4: RAMバジェット合計
# ---------------------------------------------------------------------------
def check_x4() -> tuple[float, bool]:
    text = BUDGET_FILE.read_text(encoding="utf-8")
    in_ram = False
    sizes: list[float] = []
    for line in text.splitlines():
        if re.match(r"^## 1\.", line):
            in_ram = True
            continue
        if re.match(r"^## \d+\.", line) and in_ram:
            break
        if not in_ram:
            continue
        if "合計" in line or ":---" in line:
            continue
        m = re.match(r"^\|\s*[^|]+\|\s*([\d.]+)\s*\|", line)
        if m:
            try:
                sizes.append(float(m.group(1)))
            except ValueError:
                pass
    total = sum(sizes)
    return total, total <= 64.0


# ---------------------------------------------------------------------------
# X5: IPC Router API名の表記ゆれ
# ---------------------------------------------------------------------------
_IPC_ALIASES = {
    "register_service": ["registerService", "register-service"],
    "lookup_service":   ["lookupService", "lookup-service", "look_up_service"],
    "route_message":    ["routeMessage", "route-message"],
}
_X5_SKIP = {"CONSISTENCY_MATRIX.md", "ipc_router.md", "requirement_list.md"}


def check_x5(all_files: list[Path]) -> list[tuple[Path, str, str]]:
    violations = []
    for path in all_files:
        if path.name in _X5_SKIP:
            continue
        text = path.read_text(encoding="utf-8")
        for canonical, aliases in _IPC_ALIASES.items():
            for alias in aliases:
                if alias in text:
                    violations.append((path, canonical, alias))
    return violations


# ---------------------------------------------------------------------------
# スペックマトリクス CSV（コンポーネント × 要求キーワード 2Dマトリクス）
# ---------------------------------------------------------------------------
def generate_spec_matrix() -> tuple[list[str], list[str], dict[str, set[str]]]:
    """
    コンポーネントファイル × 要求キーワード の2Dマトリクスデータを生成する。

    Returns:
        all_kw   : ソート済みキーワードリスト（列ヘッダ）
        all_files: ソート済みコンポーネントファイルパス（行）
        file_kw_map: {ファイルパス: キーワードset}
    """
    skip = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}
    comp_files = sorted(
        p for p in COMPONENTS_DIR.rglob("*.md") if p.name not in skip
    )

    defined_kw = load_defined_keywords()

    file_kw_map: dict[str, set[str]] = {}
    for f in comp_files:
        text = f.read_text(encoding="utf-8")
        kws = (extract_keywords(text) & defined_kw) - TEMPLATE_KW
        rel = str(f.relative_to(REPO_ROOT))
        file_kw_map[rel] = kws

    all_kw = sorted(set().union(*file_kw_map.values())) if file_kw_map else []
    all_files = sorted(file_kw_map.keys())

    return all_kw, all_files, file_kw_map


def write_spec_matrix_csv(all_kw: list[str], all_files: list[str],
                           file_kw_map: dict[str, set[str]]) -> None:
    """スペックマトリクスをCSVに書き出す。"""
    SPEC_MATRIX_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SPEC_MATRIX_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["component"] + all_kw)
        for fp in all_files:
            kws = file_kw_map.get(fp, set())
            writer.writerow([fp] + ["1" if k in kws else "0" for k in all_kw])


def read_spec_matrix_csv() -> tuple[list[str], list[str], dict[str, set[str]]]:
    """既存のスペックマトリクスCSVを読み込む。"""
    if not SPEC_MATRIX_CSV.exists():
        return [], [], {}
    with SPEC_MATRIX_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        all_kw = header[1:]
        all_files: list[str] = []
        file_kw_map: dict[str, set[str]] = {}
        for row in reader:
            if not row:
                continue
            fp = row[0]
            all_files.append(fp)
            kws = {all_kw[i] for i, v in enumerate(row[1: len(all_kw) + 1]) if v == "1"}
            file_kw_map[fp] = kws
    return all_kw, all_files, file_kw_map


# ---------------------------------------------------------------------------
# キーワード×セクション情報の機械的抽出
# ---------------------------------------------------------------------------
def extract_keyword_definitions(req_text: str) -> dict[str, str]:
    """
    requirement_list.md のテーブル行からキーワード → 定義テキストのマッピングを抽出する。
    テーブル形式: | `{Keyword}` | 定義文 | 優先度 | 検証方法 |
    """
    definitions: dict[str, str] = {}
    pattern = re.compile(r'^\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|\s*([^|]+?)\s*\|')
    for line in req_text.splitlines():
        m = pattern.match(line)
        if m:
            definitions[m.group(1)] = m.group(2).strip()
    return definitions


def find_sections_with_keyword(text: str, keyword: str) -> list[str]:
    """
    テキスト内で `{keyword}` を含む行が属するセクションのヘッダ行を返す。
    """
    lines = text.splitlines()
    current_header = "(ファイル先頭)"
    found: list[str] = []
    for line in lines:
        if re.match(r"^#{1,4} ", line):
            current_header = line.rstrip()
        elif f"{{{keyword}}}" in line and current_header not in found:
            found.append(current_header)
    return found


def build_keyword_section_map(
    all_files: list[str],
    file_kw_map: dict[str, set[str]],
) -> dict[str, list[dict]]:
    """
    キーワードごとに言及しているファイルとセクションヘッダを集める（Python 機械的処理）。
    2件以上のファイルで言及されているキーワードのみ返す。

    Returns: {keyword: [{"file": rel_path, "sections": [header, ...]}, ...]}
    """
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


# ---------------------------------------------------------------------------
# チェックリスト CSV（LLM生成）
# ---------------------------------------------------------------------------
def generate_checklist_from_matrix(
    all_files: list[str],
    file_kw_map: dict[str, set[str]],
) -> list[dict]:
    """
    キーワード×セクション情報（Python で機械的に抽出）を CHECKLIST.md の観点で
    LLM に解析させ、チェック項目 CSV 行を生成する。

    Returns: CHECKLIST_FIELDS に準拠した dict のリスト（llm_result/llm_reason は空）
    """
    checklist_path = COMPONENTS_DIR / "CHECKLIST.md"
    matrix_path = COMPONENTS_DIR / "CONSISTENCY_MATRIX.md"

    checklist_text = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else ""
    aspect_text = ""
    if matrix_path.exists():
        aspect_text = extract_sections(
            matrix_path.read_text(encoding="utf-8"), ["観点"], max_chars=1200
        )

    req_text = REQUIREMENT_FILE.read_text(encoding="utf-8")
    kw_definitions = extract_keyword_definitions(req_text)

    # キーワード×セクション情報を Python で機械的に構築
    kw_section_map = build_keyword_section_map(all_files, file_kw_map)

    # ベース名 → フルパスのマッピング
    name_to_path: dict[str, str] = {Path(fp).name: fp for fp in all_files}

    # 言及ファイル数が多い順に上位 35 キーワードを選択
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
        （定義済みキーワードを複数コンポーネントが参照している箇所）
        {json.dumps(kw_data, ensure_ascii=False, indent=2)}

        ## 出力ルール
        - 以下のJSON形式のみで出力すること（前置き・説明文・コードブロック記号は不要）。
        - file_a/file_b は "mentions" 内の "file" フィールドの値をそのまま使用すること。
        - file_a_section/file_b_section は "sections" 内のヘッダ文字列をそのまま使用すること。
        - check_content は「何を比較・確認するか」を日本語で具体的に記述すること。
        - aspect は A〜I の観点コードを使用すること。
        - 1ペアにつき1〜4個のチェック項目を生成すること。
        - 同一ペアへの複数キーワードからのチェックは1エントリにまとめてよい。

        {{"pairs":[{{"file_a":"path/a.md","file_b":"path/b.md","shared_keywords":["kw1"],"checks":[{{"aspect":"A","file_a_section":"## 3.1 ...","file_b_section":"## 4.1 ...","check_content":"チェック内容"}}]}}]}}
    """)

    log(f"  [LLM] チェックリスト生成中 (キーワード={len(sorted_kws)}, model={GENTABLE_MODEL})...")
    raw = call_llm(prompt, max_tokens=8192)
    log(f"  [LLM] 生レスポンス ({len(raw)} chars): {raw[:400]}")
    result = parse_llm_json(raw)

    if "error" in result or result.get("status") == "ERROR":
        print(f"  {YELLOW}[LLM] チェックリスト生成失敗: {result.get('error', result)}{RESET}")
        return []

    items: list[dict] = []
    for pair_idx, pair_data in enumerate(result.get("pairs", [])):
        fa = pair_data.get("file_a", "")
        fb = pair_data.get("file_b", "")
        # LLM がベース名のみ返した場合にフルパスへ解決
        fa = name_to_path.get(fa, name_to_path.get(Path(fa).name, fa))
        fb = name_to_path.get(fb, name_to_path.get(Path(fb).name, fb))
        shared = ",".join(pair_data.get("shared_keywords", []))
        pair_id = f"G{pair_idx + 1:02d}"

        for check_idx, check in enumerate(pair_data.get("checks", []), start=1):
            items.append({
                "pair_id": pair_id,
                "file_a": fa,
                "file_b": fb,
                "shared_keywords": shared,
                "file_a_section": check.get("file_a_section", ""),
                "file_b_section": check.get("file_b_section", ""),
                "check_num": str(check_idx),
                "aspect": check.get("aspect", ""),
                "check_content": check.get("check_content", ""),
                "llm_result": "",
                "llm_reason": "",
            })

    return items


def write_csv_checklist(items: list[dict]) -> None:
    """チェックリストをCSVに書き出す。"""
    CHECKLIST_CSV.parent.mkdir(parents=True, exist_ok=True)
    with CHECKLIST_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CHECKLIST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)


def read_csv_checklist() -> list[dict]:
    """既存のチェックリストCSVを読み込む。"""
    if not CHECKLIST_CSV.exists():
        return []
    with CHECKLIST_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ---------------------------------------------------------------------------
# LLM整合性チェック（チェックリストCSV駆動）
# ---------------------------------------------------------------------------
def check_pair_llm(pair_id: str, pair_items: list[dict]) -> dict:
    """チェックリストCSVの1ペア分を LLM で整合性チェックする。"""
    file_a_rel = pair_items[0].get("file_a", "")
    file_b_rel = pair_items[0].get("file_b", "")

    file_a_path = REPO_ROOT / file_a_rel
    file_b_path = REPO_ROOT / file_b_rel

    if not file_a_path.exists():
        return {"summary": "ERROR", "items": [], "error": f"not found: {file_a_rel}"}
    if not file_b_path.exists():
        return {"summary": "ERROR", "items": [], "error": f"not found: {file_b_rel}"}

    text_a = file_a_path.read_text(encoding="utf-8")
    text_b = file_b_path.read_text(encoding="utf-8")

    # file_a_section/file_b_section ヘッダを優先、なければ shared_keywords にフォールバック
    def _header_keywords(header: str) -> list[str]:
        """セクションヘッダから検索用キーワードを抽出する。"""
        clean = re.sub(r"^#+\s*", "", header).strip()
        return [clean] + re.findall(r"[A-Za-z_][A-Za-z0-9_]*", clean) if clean else []

    a_hints: list[str] = []
    b_hints: list[str] = []
    for item in pair_items:
        if item.get("file_a_section"):
            a_hints.extend(_header_keywords(item["file_a_section"]))
        if item.get("file_b_section"):
            b_hints.extend(_header_keywords(item["file_b_section"]))

    # セクション情報がなければ shared_keywords で代替
    if not a_hints:
        for item in pair_items:
            a_hints.extend(k.strip() for k in item.get("shared_keywords", "").split(",") if k.strip())
    if not b_hints:
        b_hints = a_hints[:]

    a_hints = list(dict.fromkeys(a_hints))
    b_hints = list(dict.fromkeys(b_hints))

    excerpt_a = extract_sections(text_a, a_hints, max_chars=3000)
    excerpt_b = extract_sections(text_b, b_hints, max_chars=3000)

    if excerpt_a == "(対象セクションが見つかりませんでした)":
        excerpt_a = text_a[:3000]
    if excerpt_b == "(対象セクションが見つかりませんでした)":
        excerpt_b = text_b[:3000]

    label = f"{Path(file_a_rel).name} × {Path(file_b_rel).name}"
    check_items = [(item["check_num"], item["check_content"]) for item in pair_items]

    return llm_check(
        pair_id, label,
        f"[{Path(file_a_rel).name}]\n{excerpt_a}",
        f"[{Path(file_b_rel).name}]\n{excerpt_b}",
        check_items,
    )


def run_llm_checks_from_items(items: list[dict]) -> int:
    """チェックリスト全項目を LLM でチェックし、結果を items に書き戻す。"""
    pairs: dict[str, list[dict]] = {}
    pair_order: list[str] = []
    for item in items:
        pid = item["pair_id"]
        if pid not in pairs:
            pairs[pid] = []
            pair_order.append(pid)
        pairs[pid].append(item)

    total_errors = 0
    for pair_id in pair_order:
        pair_items = pairs[pair_id]
        result = check_pair_llm(pair_id, pair_items)
        file_a = pair_items[0].get("file_a", "")
        file_b = pair_items[0].get("file_b", "")
        label = f"{Path(file_a).name} × {Path(file_b).name}"
        total_errors += report_llm(pair_id, label, result)

        # LLM結果を items dict に書き戻す（同一オブジェクト参照なので CSV保存時に反映される）
        result_map = {r.get("id", ""): r for r in result.get("items", [])}
        for item in pair_items:
            r = result_map.get(item["check_num"])
            if r:
                item["llm_result"] = r.get("status", "")
                item["llm_reason"] = r.get("reason", "")

    return total_errors


# ---------------------------------------------------------------------------
# レポート出力ユーティリティ
# ---------------------------------------------------------------------------
STATUS_COLOR = {"PASS": GREEN, "FAIL": RED, "WARN": YELLOW, "ERROR": RED}


def report_mechanical(title: str, items: list, formatter, warn: bool = False) -> int:
    color = YELLOW if warn else RED
    label = "WARN" if warn else "NG"
    print(f"\n{BOLD}{CYAN}[{title}]{RESET}")
    if not items:
        print(f"  {GREEN}OK — 問題なし{RESET}")
        return 0
    for item in items:
        print(f"  {color}{label}{RESET} {formatter(item)}")
    return len(items)


def report_llm(pair_id: str, label: str, result: dict) -> int:
    summary = result.get("summary", "ERROR")
    color = STATUS_COLOR.get(summary, RED)
    print(f"\n{BOLD}{CYAN}[{pair_id}: {label}]{RESET}  →  {color}{BOLD}{summary}{RESET}")

    llm_items = result.get("items", [])
    if not llm_items and "error" in result:
        error_msg = result.get("reason", result.get("raw", result.get("error", "不明")))
        print(f"  {RED}LLMエラー: {error_msg}{RESET}")
        body = result.get("body", "")
        if body:
            print(f"  {RED}  詳細: {body}{RESET}")
        return 1

    for item in llm_items:
        s = item.get("status", "?")
        c = STATUS_COLOR.get(s, RESET)
        print(f"  [{item.get('id','?')}] {c}{s}{RESET}  {item.get('reason','')}")

    return 0 if summary == "PASS" else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPO_ROOT / "tmp" / f"consistency_{timestamp}.txt"
    tee = Tee(out_path)

    print(f"{BOLD}Fireball 仕様整合性チェッカー{RESET}")
    print(f"リポジトリ: {REPO_ROOT}")
    print(f"結果ファイル: {out_path.relative_to(REPO_ROOT)}")

    if USE_LLM:
        flags = " +DEBUG" if DEBUG else ""
        flags += " +gentable" if GEN_TABLE else ""
        if USE_ANTHROPIC:
            print(f"LLMチェック: 有効 (backend=Anthropic Claude, model={ANTHROPIC_MODEL}{flags})")
        elif USE_OPENROUTER:
            print(f"LLMチェック: 有効 (backend=OpenRouter, model={GENTABLE_MODEL}{flags})")
        else:
            print(f"LLMチェック: 有効 (backend=ollama, model={CHECK_MODEL}{flags})")
    else:
        backends = []
        if ANTHROPIC_API_KEY:
            backends.append("ANTHROPIC_API_KEY")
        if OPEN_ROUTER_API_KEY:
            backends.append("OPEN_ROUTER_API_KEY")
        if backends:
            print(f"LLMチェック: 無効 (利用可能: {', '.join(backends)}; --llm で有効化)")
        else:
            print(f"{DIM}LLMチェック: 無効 (--llm で有効化){RESET}")
        if GEN_TABLE:
            print("スペックマトリクス生成: 有効 (LLMチェックリスト生成は --llm --gentable で実行)")

    # -----------------------------------------------------------------------
    # 機械的チェック (X1-X5) — 常に実行
    # -----------------------------------------------------------------------
    defined_kw = load_defined_keywords()
    component_files = collect_component_md_files()
    all_md_files = collect_all_md_files()
    total_errors = 0

    print(f"\n{'─'*60}")
    print(f"{BOLD}■ 機械的チェック (X1-X5){RESET}")
    print(f"{'─'*60}")

    x1 = check_x1(defined_kw, component_files)
    total_errors += report_mechanical(
        "X1 未定義キーワード参照", x1,
        lambda e: f"{e[0].relative_to(REPO_ROOT)}  →  {{{e[1]}}} が requirement_list.md に未定義",
    )

    x2 = sorted(check_x2(defined_kw, all_md_files))
    report_mechanical(
        "X2 孤立キーワード（仕様書に引用なし）", x2,
        lambda kw: f"{{{kw}}} はどのコンポーネント仕様書にも引用されていない",
        warn=True,
    )
    # X2 は警告のみ（エラーカウントに含めない）

    x3 = check_x3(component_files)
    total_errors += report_mechanical(
        "X3 Tier規制違反（実装依存文脈での上位Tier型参照）", x3,
        lambda e: f"{e[0].relative_to(REPO_ROOT)} ({e[1]}) が上位Tier型 '{e[2]}' を実装依存文脈で参照",
    )

    total_ram, ok = check_x4()
    print(f"\n{BOLD}{CYAN}[X4 RAMバジェット合計]{RESET}")
    if ok:
        print(f"  {GREEN}OK{RESET} — 合計 {total_ram:.1f} KB (<= 64 KB)")
    else:
        print(f"  {RED}NG{RESET} — 合計 {total_ram:.1f} KB が 64 KB を超過")
        total_errors += 1

    x5 = check_x5(all_md_files)
    total_errors += report_mechanical(
        "X5 IPC Router API名の表記ゆれ", x5,
        lambda e: f"{e[0].relative_to(REPO_ROOT)}  →  '{e[2]}' (正式名: {e[1]})",
    )

    # -----------------------------------------------------------------------
    # スペックマトリクス生成 (--gentable)
    # -----------------------------------------------------------------------
    checklist_items: list[dict] = []

    if GEN_TABLE:
        print(f"\n{'─'*60}")
        print(f"{BOLD}■ スペックマトリクス生成 (--gentable){RESET}")
        print(f"{'─'*60}")

        all_kw, all_files, file_kw_map = generate_spec_matrix()
        write_spec_matrix_csv(all_kw, all_files, file_kw_map)
        rel = SPEC_MATRIX_CSV.relative_to(REPO_ROOT)
        print(f"  {GREEN}生成完了{RESET}: {rel}")
        print(f"  コンポーネント: {len(all_files)} ファイル  /  キーワード: {len(all_kw)} 種")

        # --llm と組み合わせた場合のみチェックリストも生成
        if USE_LLM:
            print(f"\n{'─'*60}")
            print(f"{BOLD}■ LLMによるチェックリスト生成{RESET}")
            print(f"{'─'*60}")

            kw_section_map = build_keyword_section_map(all_files, file_kw_map)
            print(f"  複数コンポーネントで共有されるキーワード: {len(kw_section_map)} 種")

            checklist_items = generate_checklist_from_matrix(all_files, file_kw_map)
            if checklist_items:
                write_csv_checklist(checklist_items)
                rel = CHECKLIST_CSV.relative_to(REPO_ROOT)
                print(f"  {GREEN}生成完了{RESET}: {rel}  ({len(checklist_items)} 項目)")
            else:
                print(f"  {YELLOW}チェックリスト生成結果が空です{RESET}")

    # -----------------------------------------------------------------------
    # LLM整合性チェック (--llm)
    # -----------------------------------------------------------------------
    if USE_LLM:
        # --gentable で生成済みなら流用、なければ既存CSVを読み込む
        if not checklist_items:
            checklist_items = read_csv_checklist()

        print(f"\n{'─'*60}")
        if USE_ANTHROPIC:
            backend, check_model_name = "Anthropic Claude", ANTHROPIC_MODEL
        elif USE_OPENROUTER:
            backend, check_model_name = "OpenRouter", GENTABLE_MODEL
        else:
            backend, check_model_name = "ollama", CHECK_MODEL
        print(f"{BOLD}■ LLM整合性チェック  backend={backend}, model={check_model_name}{RESET}")
        print(f"{'─'*60}")

        if not checklist_items:
            csv_rel = CHECKLIST_CSV.relative_to(REPO_ROOT)
            print(f"  {YELLOW}警告: チェックリストCSVが見つかりません{RESET}")
            print(f"  先に --llm --gentable でチェックリストを生成してください: {csv_rel}")
        else:
            print(f"  チェック項目: {len(checklist_items)} 件")
            total_errors += run_llm_checks_from_items(checklist_items)
            write_csv_checklist(checklist_items)
            rel = CHECKLIST_CSV.relative_to(REPO_ROOT)
            print(f"\n  LLM結果をCSVに保存しました: {rel}")

    # -----------------------------------------------------------------------
    # サマリー
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    if total_errors == 0:
        print(f"{GREEN}{BOLD}チェック完了: 問題なし{RESET}")
    else:
        print(f"{RED}{BOLD}チェック完了: {total_errors} 件の問題を検出{RESET}")
        print(f"詳細: docs/components/CONSISTENCY_MATRIX.md を参照")
    print(f"{'='*60}")
    saved = tee.close()
    print(f"結果を保存しました: {saved}", file=sys.stdout)
    sys.exit(0 if total_errors == 0 else 1)


if __name__ == "__main__":
    main()
