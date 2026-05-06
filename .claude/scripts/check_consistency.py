#!/usr/bin/env python3
"""
Fireball 仕様整合性チェッカー

機械的チェック (X1-X5) と LLM意味チェック (L1-L4) を組み合わせて
docs/components/CONSISTENCY_MATRIX.md の各項目を検証する。

使い方:
    python3 .claude/scripts/check_consistency.py [--verbose] [--debug] [--llm] [--model MODEL]

オプション:
    --verbose    詳細ログを表示
    --debug      デバッグログを表示（送信ペイロード全文・レスポンス全文）
    --llm        LLM による意味チェック (L1-L4) を実行
    --model M    使用するLLMモデル
               OPEN_ROUTER_API_KEY が設定されていれば OpenRouter を使用、
               否ければ ollama を使用 (デフォルト: qwen2.5-coder:3b)
"""

import io
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

KEYWORD_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")
TIER_PATTERN = re.compile(r"\*\*Tier (\d+)")

VERBOSE = "--verbose" in sys.argv
DEBUG = "--debug" in sys.argv
USE_LLM = "--llm" in sys.argv

# LLMバックエンドの決定
OPEN_ROUTER_API_KEY = os.environ.get("OPEN_ROUTER_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

if ANTHROPIC_API_KEY:
    USE_OPENROUTER = False
    USE_ANTHROPIC = True
    MODEL = "claude-opus-4-7"
elif OPEN_ROUTER_API_KEY:
    USE_OPENROUTER = True
    USE_ANTHROPIC = False
    MODEL = "google/gemini-3.1-flash-lite-preview"
else:
    USE_OPENROUTER = False
    USE_ANTHROPIC = False
    MODEL = "qwen2.5-coder:3b"

# --model オプションで上書き
for i, a in enumerate(sys.argv):
    if a == "--model" and i + 1 < len(sys.argv):
        MODEL = sys.argv[i + 1]

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
# LLM API (ollama または OpenRouter)
# ---------------------------------------------------------------------------
def call_llm(prompt: str) -> str:
    """LLMにプロンプトを送り、レスポンス文字列を返す。"""
    if USE_ANTHROPIC:
        return call_anthropic(prompt)
    elif USE_OPENROUTER:
        return call_openrouter(prompt)
    else:
        return call_ollama(prompt)


def call_anthropic(prompt: str) -> str:
    """Anthropic Claude APIにプロンプトを送り、レスポンス文字列を返す。"""
    try:
        import anthropic
    except ImportError:
        return f'{{"error": "anthropic パッケージがインストールされていません (pip install anthropic)"}}'

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=MODEL,
            max_tokens=768,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        content = message.content[0].text if message.content else ""
        return content.strip()
    except Exception as e:
        return f'{{"error": "{type(e).__name__}: {e}"}}'


def call_ollama(prompt: str) -> str:
    """ollamaにプロンプトを送り、レスポンス文字列を返す。"""
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 768},
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


def call_openrouter(prompt: str) -> str:
    """OpenRouter APIにプロンプトを送り、レスポンス文字列を返す。"""
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 768,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPEN_ROUTER_API_KEY}",
    }

    print(f"  {DIM}[OpenRouter] POST {OPENROUTER_URL}{RESET}")
    print(f"  {DIM}[OpenRouter] model={MODEL}, payload={len(data)} bytes{RESET}")
    if DEBUG:
        debug(f"[OpenRouter] request payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")

    req = urllib.request.Request(OPENROUTER_URL, data=data, method="POST",
                                  headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw_body = resp.read()
            print(f"  {DIM}[OpenRouter] HTTP 200 OK, response={len(raw_body)} bytes{RESET}")
            debug(f"[OpenRouter] response body:\n{raw_body.decode('utf-8', errors='replace')}")
            body = json.loads(raw_body)
            choices = body.get("choices", [])
            if choices and len(choices) > 0:
                return choices[0].get("message", {}).get("content", "").strip()
            print(f"  {YELLOW}[OpenRouter] WARNING: choices が空です。フルレスポンス:{RESET}")
            print(f"  {YELLOW}{json.dumps(body, ensure_ascii=False)[:1000]}{RESET}")
            return f'{{"error": "No choices in response", "full_response": {json.dumps(body)}}}'
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"  {RED}[OpenRouter] HTTP エラー: {e.code} {e.reason}{RESET}")
        print(f"  {RED}[OpenRouter] レスポンスボディ:{RESET}")
        print(f"  {RED}{error_body}{RESET}")
        try:
            parsed = json.loads(error_body)
            return json.dumps({"error": f"HTTP {e.code}: {e.reason}", "detail": parsed})
        except json.JSONDecodeError:
            return json.dumps({"error": f"HTTP {e.code}: {e.reason}", "body": error_body})
    except urllib.error.URLError as e:
        print(f"  {RED}[OpenRouter] 接続エラー: {e.reason}{RESET}")
        return json.dumps({"error": f"URLError: {e.reason}"})
    except Exception as e:
        print(f"  {RED}[OpenRouter] 予期しないエラー: {type(e).__name__}: {e}{RESET}")
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


def parse_llm_json(raw: str) -> dict:
    """LLMのレスポンスからJSONを抽出してパースする。"""
    # コードブロックを除去
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`")
    # 最初の { から最後の } まで抽出
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"status": "ERROR", "reason": "JSONを解析できませんでした", "raw": raw[:200]}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        return {"status": "ERROR", "reason": str(e), "raw": raw[:200]}


_LLM_PREAMBLE = textwrap.dedent("""\
    あなたはFireballプロジェクトの仕様書整合性チェッカーです。
    2つの仕様書の抜粋を比較し、指定された観点で整合性を判定してください。

    【出力ルール】
    - 以下のJSON形式のみで回答すること。説明文・前置き・コードブロックは不要。
    - statusは PASS（整合）, FAIL（矛盾あり）, WARN（記述不足/判断不能）のいずれか。

    出力例:
    {"items":[{"id":"1","status":"PASS","reason":"両方の文書で同じ値を使用"},{"id":"2","status":"FAIL","reason":"Aでは5KB、Bでは8KBと記述が異なる"}],"summary":"FAIL"}

""")


def llm_check(pair_id: str, label: str, excerpt_a: str, excerpt_b: str,
              items: list[tuple[str, str]]) -> list[dict]:
    """LLMで整合性チェックを実施し、結果のリストを返す。"""
    items_text = "\n".join(f"- [{i}] {desc}" for i, (i, desc) in enumerate(
        [(str(n + 1), d) for n, (_, d) in enumerate(items)], start=0
    ))
    # items_text を正しく生成し直す
    items_text = "\n".join(f"- [{item_id}] {desc}" for item_id, desc in items)

    prompt = _LLM_PREAMBLE + textwrap.dedent(f"""\
        ## チェック対象: {pair_id} - {label}

        ### 仕様書 A の抜粋
        {excerpt_a}

        ### 仕様書 B の抜粋
        {excerpt_b}

        ### チェック項目（各項目を判定してください）
        {items_text}

        上記の仕様書AとBの抜粋を根拠として、各チェック項目のstatusとreasonを含むJSONのみを出力してください。
    """)

    log(f"  [LLM] {pair_id} にプロンプト送信中 (model={MODEL})...")
    raw = call_llm(prompt)
    log(f"  [LLM] 生レスポンス ({len(raw)} chars): {raw[:500]}")
    result = parse_llm_json(raw)
    if "error" in result or result.get("status") == "ERROR":
        print(f"  {YELLOW}[LLM] パース失敗。生レスポンス全文:{RESET}")
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
# X3: Tier規制違反（実装依存の文脈での上位Tier型参照）
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
# L1: os_coos.md × os_event_driven.md
# ---------------------------------------------------------------------------
def check_l1() -> dict:
    coos_path = COMPONENTS_DIR / "core" / "os_coos.md"
    event_path = COMPONENTS_DIR / "os_event_driven.md"
    coos_text = load_doc(coos_path)
    event_text = load_doc(event_path)

    excerpt_a = extract_sections(coos_text,
        ["コンセプト", "アルゴリズム", "CSP Handoff", "状態遷移", "検証"])
    excerpt_b = extract_sections(event_text,
        ["コンセプト", "IPC", "Call", "Reply", "状態", "異常系"])

    return llm_check(
        "L1", "os_coos.md × os_event_driven.md",
        f"[os_coos.md]\n{excerpt_a}",
        f"[os_event_driven.md]\n{excerpt_b}",
        [
            ("1", "IPCの基本方式が統一されているか: os_coos は send/recv (CSPチャネル直接)、os_event_driven は call/reply (EventQueue経由)。どちらが正規かドキュメントに明示されているか、または両者が矛盾なく共存できる関係にあるか"),
            ("2", "タスク状態名の対応: os_coos の状態 (BLOCKED等) と os_event_driven の状態 (BLOCKED_CALL/BLOCKED_REPLY) が矛盾なく定義されているか"),
            ("3", "CSP_Handoff の整合: os_coos の「直接スイッチ」と os_event_driven の「EventQueue経由」で {CSP_Handoff} の意味が矛盾しないか"),
        ],
    )


# ---------------------------------------------------------------------------
# L2: os_event_driven.md × ipc_router.md
# ---------------------------------------------------------------------------
def check_l2() -> dict:
    event_path = COMPONENTS_DIR / "os_event_driven.md"
    ipc_path = COMPONENTS_DIR / "interface" / "ipc_router.md"
    event_text = load_doc(event_path)
    ipc_text = load_doc(ipc_path)

    excerpt_a = extract_sections(event_text,
        ["IPC", "Call", "Reply", "所有権", "異常系", "キュー満杯"])
    excerpt_b = extract_sections(ipc_text,
        ["route_message", "所有権", "Handoff", "Rollback", "アルゴリズム"])

    return llm_check(
        "L2", "os_event_driven.md × ipc_router.md",
        f"[os_event_driven.md]\n{excerpt_a}",
        f"[ipc_router.md]\n{excerpt_b}",
        [
            ("1", "route_message と call/reply の関係: ipc_router の route_message は os_event_driven の call/reply の内部実装か、それとも別の経路か。関係が明示されているか"),
            ("2", "所有権移譲プロトコルの一致: ipc_router の Revoke/Enqueue/Grant と os_event_driven の message_owner 管理が同一プロトコルを記述しているか"),
            ("3", "{CSP_Handoff}（直接スイッチ）と EventQueue 経由の矛盾: ipc_router は待機中相手に即時スイッチと記述するが、EventQueue モデルでは必ずキューを経由する。矛盾があるか"),
            ("4", "キュー満杯時の動作の整合: ipc_router の「所有権を返却（Restore）」と os_event_driven の「BLOCKED_REPLY へ遷移」が同じシナリオを一貫して記述しているか"),
        ],
    )


# ---------------------------------------------------------------------------
# L3: os_scheduler.md × os_event_driven.md
# ---------------------------------------------------------------------------
def check_l3() -> dict:
    sched_path = COMPONENTS_DIR / "core" / "os_scheduler.md"
    event_path = COMPONENTS_DIR / "os_event_driven.md"
    sched_text = load_doc(sched_path)
    event_text = load_doc(event_path)

    excerpt_a = extract_sections(sched_text,
        ["状態遷移", "アルゴリズム", "BLOCKED", "READY", "RUNNING", "INTERRUPTED"])
    excerpt_b = extract_sections(event_text,
        ["状態", "BLOCKED_CALL", "BLOCKED_REPLY", "TCB", "IPC", "Call", "Reply"])

    return llm_check(
        "L3", "os_scheduler.md × os_event_driven.md",
        f"[os_scheduler.md]\n{excerpt_a}",
        f"[os_event_driven.md]\n{excerpt_b}",
        [
            ("1", "BLOCKED_CALL/BLOCKED_REPLY の欠落: os_scheduler.md の状態遷移図に BLOCKED_CALL と BLOCKED_REPLY が存在するか、または BLOCKED で代替されているか"),
            ("2", "BLOCKED_REPLY からの復帰条件: キューに空きができた際に BLOCKED_REPLY → READY へ遷移する条件が os_scheduler.md に記述されているか"),
            ("3", "ISR イベント投入: os_scheduler の notify_interrupt API と os_event_driven の ISR → EventQueue enqueue の対応関係が整合しているか"),
        ],
    )


# ---------------------------------------------------------------------------
# L4: architecture_overview.md × resource_budget.md
# ---------------------------------------------------------------------------
def check_l4() -> dict:
    arch_text = load_doc(ARCH_FILE)
    budget_text = load_doc(BUDGET_FILE)

    excerpt_a = extract_sections(arch_text, ["ヒープ", "パーティション", "メモリ"])
    excerpt_b = extract_sections(budget_text, ["メモリ予算", "RAM", "パーティション"])

    return llm_check(
        "L4", "architecture_overview.md × resource_budget.md",
        f"[architecture_overview.md]\n{excerpt_a}",
        f"[resource_budget.md]\n{excerpt_b}",
        [
            ("1", "パーティション名の対応: architecture_overview の「vSoCヒープ」と resource_budget の「vSoCメタデータ」は同一パーティションを指しているか"),
            ("2", "各パーティションの責務記述: 「ネイティブヒープ」「サブシステムヒープ」等の責務欄の記述が両ドキュメントで一貫しているか"),
            ("3", "サイズの一致: 各パーティションのサイズ (KB) が両ドキュメントで同じ数値か"),
        ],
    )


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

    items = result.get("items", [])
    if not items and "error" in result:
        error_msg = result.get('reason', result.get('raw', result.get('error', '不明なエラー')))
        error_body = result.get('body', '')
        print(f"  {RED}LLMエラー: {error_msg}{RESET}")
        if error_body:
            print(f"  {RED}  詳細: {error_body}{RESET}")
        return 1

    for item in items:
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
        if USE_ANTHROPIC:
            backend = "Anthropic Claude"
        elif USE_OPENROUTER:
            backend = "OpenRouter"
        else:
            backend = "ollama"
        debug_flag = " +DEBUG" if DEBUG else ""
        print(f"LLMチェック: 有効 (backend={backend}, model={MODEL}{debug_flag})")
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

    defined_kw = load_defined_keywords()
    component_files = collect_component_md_files()
    all_files = collect_all_md_files()
    total_errors = 0

    print(f"\n{'─'*60}")
    print(f"{BOLD}■ 機械的チェック (X1-X5){RESET}")
    print(f"{'─'*60}")

    x1 = check_x1(defined_kw, component_files)
    total_errors += report_mechanical(
        "X1 未定義キーワード参照", x1,
        lambda e: f"{e[0].relative_to(REPO_ROOT)}  →  {{{e[1]}}} が requirement_list.md に未定義",
    )

    x2 = sorted(check_x2(defined_kw, all_files))
    report_mechanical(
        "X2 孤立キーワード（仕様書に引用なし）", x2,
        lambda kw: f"{{{kw}}} はどのコンポーネント仕様書にも引用されていない",
        warn=True,
    )
    # X2は警告のみ（エラーカウントには含めない）

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

    x5 = check_x5(all_files)
    total_errors += report_mechanical(
        "X5 IPC Router API名の表記ゆれ", x5,
        lambda e: f"{e[0].relative_to(REPO_ROOT)}  →  '{e[2]}' (正式名: {e[1]})",
    )

    if USE_LLM:
        print(f"\n{'─'*60}")
        if USE_ANTHROPIC:
            backend = "Anthropic Claude"
        elif USE_OPENROUTER:
            backend = "OpenRouter"
        else:
            backend = "ollama"
        print(f"{BOLD}■ LLM意味チェック (L1-L4)  backend={backend}, model={MODEL}{RESET}")
        print(f"{'─'*60}")

        checks = [
            ("L1", "os_coos.md × os_event_driven.md", check_l1),
            ("L2", "os_event_driven.md × ipc_router.md", check_l2),
            ("L3", "os_scheduler.md × os_event_driven.md", check_l3),
            ("L4", "architecture_overview.md × resource_budget.md", check_l4),
        ]
        for pair_id, label, fn in checks:
            result = fn()
            total_errors += report_llm(pair_id, label, result)

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
