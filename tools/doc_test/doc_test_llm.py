#!/usr/bin/env python3
"""
Fireball Documentation LLM Auto-Tester (doc_test_llm.py)

This script uses LLMs (Gemini, OpenRouter, Sakura, or Ollama) to semantically audit
markdown documentation (component specifications and requirement definitions) in the Fireball repository.
It performs checks on:
  1. Development Policy & Memory/STL compliance (no heap, RAII, flat_map, etc.)
  2. Requirement Traceability (satisfiability of requirement keywords {Keyword})
  3. Quality & Completeness (vagueness, placeholders like TBD/TODO, ellipsis)
"""

import os
import sys
import re
import json
import argparse
import urllib.request
import urllib.error
from pathlib import Path

# Optional: rich for beautiful console outputs
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import print as rprint
    USE_RICH = True
    console = Console()
except ImportError:
    USE_RICH = False

# Section-based matrix review support (new feature)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from extract_sections import extract_sections_from_file, Section
    from build_section_matrix import match_sections
    SECTION_MATRIX_AVAILABLE = True
except ImportError:
    SECTION_MATRIX_AVAILABLE = False

# Default configurations
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # tools/doc_test/doc_test_llm.py -> repo root
DOCS_DIR = REPO_ROOT / "docs"
COMPONENTS_DIR = DOCS_DIR / "components"
REQUIREMENT_FILE = DOCS_DIR / "requires" / "requirement_list.md"

# LLM backends & models
SAKURA_MODEL = "gpt-oss-120b"
OPEN_ROUTER_MODEL = "google/gemma-4-31b-it:free"
GEMINI_MODEL = "gemini-3.1-flash-lite"
OLLAMA_MODEL = "qwen2.5-coder:3b"

OLLAMA_URL = "http://localhost:11434/api/generate"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SAKURA_URL = "https://api.ai.sakura.ad.jp/v1/chat/completions"
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

KEYWORD_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")


def log_info(msg: str):
    if USE_RICH:
        console.print(f"[bold blue]INFO:[/bold blue] {msg}")
    else:
        print(f"INFO: {msg}")


def log_success(msg: str):
    if USE_RICH:
        console.print(f"[bold green]SUCCESS:[/bold green] {msg}")
    else:
        print(f"SUCCESS: {msg}")


def log_warn(msg: str):
    if USE_RICH:
        console.print(f"[bold yellow]WARN:[/bold yellow] {msg}")
    else:
        print(f"WARN: {msg}")


def log_error(msg: str):
    if USE_RICH:
        console.print(f"[bold red]ERROR:[/bold red] {msg}", file=sys.stderr)
    else:
        print(f"ERROR: {msg}", file=sys.stderr)


# LLM Calling Functions
def call_sakura(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "service_tier": "flex"
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(SAKURA_URL, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"].strip()


def call_openrouter(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "service_tier": "flex"
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(OPENROUTER_URL, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"].strip()


def call_gemini(prompt: str, api_key: str, model: str) -> str:
    url = GEMINI_URL_TEMPLATE.format(model=model, key=api_key)
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
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body["candidates"][0]["content"]["parts"][0]["text"].strip()


def call_ollama(prompt: str, model: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": max_tokens},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        return body.get("response", "").strip()


def call_llm(prompt: str, args) -> str:
    """Dispatches the prompt to the selected LLM backend."""
    # Read environment variables
    sakura_key = os.environ.get("SAKURA_AI_API_KEY", "").strip()
    openrouter_key = os.environ.get("OPEN_ROUTER_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", "")).strip()

    # Determine backend
    backend = args.backend
    if not backend:
        if sakura_key:
            backend = "sakura"
        elif openrouter_key:
            backend = "openrouter"
        elif gemini_key:
            backend = "gemini"
        else:
            backend = "ollama"

    try:
        if backend == "sakura":
            if not sakura_key:
                raise ValueError("SAKURA_AI_API_KEY environment variable is not set.")
            model = args.model or SAKURA_MODEL
            return call_sakura(prompt, sakura_key, model, args.max_tokens)
        elif backend == "openrouter":
            if not openrouter_key:
                raise ValueError("OPEN_ROUTER_API_KEY environment variable is not set.")
            model = args.model or OPEN_ROUTER_MODEL
            return call_openrouter(prompt, openrouter_key, model, args.max_tokens)
        elif backend == "gemini":
            if not gemini_key:
                raise ValueError("GEMINI_API_KEY / GOOGLE_API_KEY environment variable is not set.")
            model = args.model or GEMINI_MODEL
            return call_gemini(prompt, gemini_key, model)
        else:
            model = args.model or OLLAMA_MODEL
            return call_ollama(prompt, model, args.max_tokens)
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API Error (HTTP {e.code}): {e.reason}\nBody: {err_content}")
    except Exception as e:
        raise RuntimeError(f"Failed to communicate with LLM backend '{backend}': {e}")


def parse_llm_json_response(raw_text: str) -> dict:
    """Helper to extract JSON object from LLM response markdown or raw text."""
    # Try finding json block
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if json_match:
        text_to_parse = json_match.group(1)
    else:
        # Fallback to direct parse or finding first { and last }
        start = raw_text.find('{')
        end = raw_text.rfind('}')
        if start != -1 and end != -1:
            text_to_parse = raw_text[start:end+1]
        else:
            text_to_parse = raw_text

    try:
        return json.loads(text_to_parse)
    except json.JSONDecodeError as e:
        return {
            "status": "ERROR",
            "reason": f"Failed to parse LLM JSON response. Error: {e}",
            "suggestions": f"Raw LLM output was:\n{raw_text}"
        }


# Data Parsing Functions
def load_requirement_keywords() -> dict[str, str]:
    """Parses docs/requires/requirement_list.md and docs/architecture/document_structure.md and returns a mapping of Keyword -> description."""
    keywords = {}
    
    # 1. Parse requirement_list.md
    if REQUIREMENT_FILE.exists():
        with open(REQUIREMENT_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        rows = re.findall(r"\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|\s*([^|]+)\s*\|", content)
        for kw, desc in rows:
            keywords[kw.strip()] = desc.strip()
    else:
        log_warn(f"Requirement file not found at {REQUIREMENT_FILE}")

    # 2. Parse document_structure.md for meta-keywords
    doc_struct = DOCS_DIR / "architecture" / "document_structure.md"
    if doc_struct.exists():
        with open(doc_struct, "r", encoding="utf-8") as f:
            content = f.read()
        rows = re.findall(r"\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|\s*([^|]+)\s*\|", content)
        for kw, desc in rows:
            keywords[kw.strip()] = desc.strip()
    else:
        log_warn(f"Document structure file not found at {doc_struct}")

    return keywords


def load_meta_keywords() -> set[str]:
    """Parses docs/architecture/document_structure.md and returns a set of meta-keywords."""
    doc_struct = DOCS_DIR / "architecture" / "document_structure.md"
    meta_kws = set()
    if doc_struct.exists():
        try:
            content = doc_struct.read_text(encoding="utf-8")
            meta_kws = set(re.findall(r"\{([A-Za-z0-9_]+)\}", content))
        except Exception as e:
            log_warn(f"Failed to load meta keywords from {doc_struct}: {e}")
    if not meta_kws:
        meta_kws = {
            "3TierSeparation", "ConfigurableSystem", "FaultIsolation", "RecoveryStrategy", 
            "RestrictedPhysicalAccess", "StaticDI", "AI_Native_Dev", "Risk_Tiering", 
            "SpecificationFirst", "ZeroOverhead", "ZeroCostAbstraction", "Static_Resolution", 
            "CompileTimeValidation", "NoStdVector", "BumpAllocator", "FlatMapIndexed", 
            "BinarySearch", "AccessDictionary"
        }
    return meta_kws



def load_project_policies() -> dict[str, str]:
    """Loads guidelines from .claude/rules folder for policy verification."""
    rules_dir = REPO_ROOT / ".claude" / "rules"
    policies = {}
    policy_files = ["development-policy.md", "stdlib_policy.md", "embedded_cpp.md"]

    for pf in policy_files:
        path = rules_dir / pf
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            # Strip YAML frontmatter if present
            if text.startswith("---") or (text.startswith("name:") and "---" in text):
                parts = text.split("---", 1)
                text = parts[1] if len(parts) > 1 else text
            policies[pf] = text.strip()
        else:
            policies[pf] = "(Policy file not found)"
    return policies


def perform_policy_check(doc_path: Path, doc_content: str, policies: dict[str, str], args) -> dict:
    """LLM Check 1: Verify document against project's Development & C++ memory policies."""
    dev_policy = policies.get("development-policy.md", "")
    stdlib_policy = policies.get("stdlib_policy.md", "")
    embedded_policy = policies.get("embedded_cpp.md", "")

    prompt = f"""\
あなたは高品質なC++23組み込みシステム(Fireball Hypervisor)の仕様書査読者です。
対象ドキュメントの内容が、以下のプロジェクト開発ポリシーおよびメモリ/STL規約に違反していないか検証してください。

【プロジェクト規約】
<DEVELOPMENT_POLICY>
{dev_policy[:3000]}
</DEVELOPMENT_POLICY>

<STDLIB_POLICY>
{stdlib_policy[:3000]}
</STDLIB_POLICY>

<EMBEDDED_CPP_POLICY>
{embedded_policy[:2000]}
</EMBEDDED_CPP_POLICY>

【対象ドキュメント】
ファイル名: {doc_path.name}
---
{doc_content[:6000]}
---

【検証項目】
1. 動的メモリ確保（ヒープ）や、`std::vector`や`std::string`などの動的コンテナの無意識な使用・推奨をしていないか。
2. 例外（try/catch/throw）やRTTIの使用に言及していないか。
3. C++のモダンな設計方針（constexpr, flat_map, Conceptsなど）の活用について、ポリシーに違反していないか。

【出力フォーマット】
以下のJSONフォーマットのみで回答してください。JSON以外のテキストは一切出力しないでください。
{{
  "status": "PASS" または "FAIL",
  "reason": "PASSの場合は『開発ポリシーおよびメモリ制約に適合している』旨、FAILの場合は具体的な違反箇所（セクション名や行）と理由",
  "suggestions": "改善のための具体的な修正案（Markdownコードブロック等）"
}}
"""
    raw_response = call_llm(prompt, args)
    return parse_llm_json_response(raw_response)


def perform_traceability_check(doc_path: Path, doc_content: str, doc_kws: list[str], kw_defs: dict[str, str], args) -> dict:
    """LLM Check 2: Verify if sections with keyword tags satisfy the requirement descriptions."""
    if not doc_kws:
        return {
            "status": "PASS",
            "reason": "ドキュメント内に要求キーワード {Keyword} が見つからないため、検証をスキップします。",
            "suggestions": ""
        }

    # Extract only keyword definitions relevant to this document
    relevant_defs = {kw: kw_defs[kw] for kw in doc_kws if kw in kw_defs}
    if not relevant_defs:
        return {
            "status": "PASS",
            "reason": f"ドキュメント内のキーワード {doc_kws} に対応する定義が requirement_list.md にありません。",
            "suggestions": ""
        }

    kw_context = "\n".join([f"- `{{{kw}}}`: {desc}" for kw, desc in relevant_defs.items()])

    prompt = f"""\
あなたはFireballの仕様整合性チェッカーです。
対象のドキュメントの各セクションに付与された要求キーワード `{{Keyword}}` について、ドキュメントの記述がその要求事項の要件を満たしているか、また矛盾がないかを検証してください。

【要求定義リスト】
{kw_context}

【対象ドキュメント】
ファイル名: {doc_path.name}
---
{doc_content[:6000]}
---

【出力フォーマット】
以下のJSONフォーマットのみで回答してください。JSON以外のテキストは一切出力しないでください。
{{
  "status": "PASS" または "FAIL",
  "reason": "PASSの場合は『全ての要求キーワードが正しく充足されている』旨、FAILの場合はどのキーワードが不足・矛盾しているかその理由",
  "suggestions": "改善のための具体的な修正案"
}}
"""
    raw_response = call_llm(prompt, args)
    return parse_llm_json_response(raw_response)


def perform_quality_check(doc_path: Path, doc_content: str, args) -> dict:
    """LLM Check 3: Check for vagueness, placeholders (TBD, TODO, ellipsis), and completeness."""
    prompt = f"""\
あなたはFireballの仕様査読者です。
対象ドキュメントについて、以下の品質チェックを行ってください。

【チェック内容】
1. プレースホルダー（TBD, TODO, ... 等）が残っていないか。
2. 曖昧な記述（「適切な処理」「必要に応じて」「など」等で具体的な動作や仕様が濁されている部分）がないか。
3. 設計やAPI定義において、引数や戻り値の型や説明が欠落している箇所がないか。

【対象ドキュメント】
ファイル名: {doc_path.name}
---
{doc_content[:6000]}
---

【出力フォーマット】
以下のJSONフォーマットのみで回答してください。JSON以外のテキストは一切出力しないでください。
{{
  "status": "PASS" または "FAIL",
  "reason": "PASSの場合は『プレースホルダーや曖昧な記述がなく十分具体的である』旨、FAILの場合は検出された具体的な問題点（該当する行やセクション）と理由",
  "suggestions": "改善のための具体的な修正案"
}}
"""
    raw_response = call_llm(prompt, args)
    return parse_llm_json_response(raw_response)


def run_checks_on_file(doc_path: Path, policies: dict[str, str], kw_defs: dict[str, str], args) -> dict:
    """Runs all 3 checks on a single document file."""
    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {
            "file": str(doc_path.relative_to(REPO_ROOT)),
            "error": f"Failed to read file: {e}",
            "checks": {}
        }

    # Extract all {Keyword} matches in the doc
    doc_kws = list(set(KEYWORD_PATTERN.findall(content)))

    # Filter out meta-keywords from traceability check
    meta_kws = load_meta_keywords()
    trace_kws = [kw for kw in doc_kws if kw not in meta_kws]

    results = {}
    try:
        # Check 1: Policies & C++ Constraints
        results["policy"] = perform_policy_check(doc_path, content, policies, args)

        # Check 2: Requirements Traceability
        results["traceability"] = perform_traceability_check(doc_path, content, trace_kws, kw_defs, args)

        # Check 3: Quality & Completeness
        results["quality"] = perform_quality_check(doc_path, content, args)
    except Exception as e:
        results["error"] = f"LLM Execution error: {e}"

    return {
        "file": str(doc_path.relative_to(REPO_ROOT)),
        "keywords": doc_kws,
        "checks": results
    }


def perform_pair_check(file_a: Path, content_a: str, file_b: Path, content_b: str, args) -> dict:
    """LLM Check for combination mode between two files."""
    prompt = f"""\
あなたは2つの仕様書の整合性を監査するリードアーキテクトです。

【入力】
仕様書A: {file_a.name}
---
{content_a[:6000]}
---

仕様書B: {file_b.name}
---
{content_b[:6000]}
---

【検証項目】
仕様書Aと仕様書Bの設計が、以下の観点で矛盾なく統合されているか検証してください。
1. API/I/F整合性: 関数名、引数、型、エラーハンドリング方針の一致
2. 状態遷移・ライフサイクル: 送受信、同期、所有権移譲などのタイミングやプロトコルの齟齬
3. データ構造とメモリ: 共有バッファの解釈やサイズの不一致

【出力フォーマット】
以下のJSONフォーマットのみで回答してください。JSON以外のテキストは一切出力しないでください。
{{
  "status": "PASS" または "FAIL",
  "reason": "合否の具体的な根拠。FAILの場合は不整合がある箇所とその理由",
  "suggestions": "不整合を解消するための具体的なドキュメント修正案 (Markdown)"
}}
"""
    raw_response = call_llm(prompt, args)
    return parse_llm_json_response(raw_response)


def perform_hierarchy_check(parent_path: Path, parent_content: str, child_path: Path, child_content: str, args) -> dict:
    """LLM Check for hierarchy mode between parent and child layer."""
    prompt = f"""\
あなたはシステムの階層化アーキテクチャの監査スペシャリストです。

【入力】
上位レイヤー仕様（要求・コア定義）: {parent_path.name}
---
{parent_content[:6000]}
---

下位レイヤー仕様（具象実装設計）: {child_path.name}
---
{child_content[:6000]}
---

【検証項目】
上位レイヤーと下位レイヤーの定義において、以下の階層化原則が守られているか検証してください。
1. Abstraction (抽象化): 下位レイヤーの具象ハードウェアや実装の都合が、上位レイヤーに漏れ出していないか (Leak of Abstraction)
2. Dependency (依存関係): 上位レイヤーが下位の具象モジュールに直接依存していないか。Static DI (Harness) が適切に設計されているか
3. Detail Trace (具体化): 上位の要求が下位レイヤーで矛盾なく詳細化されているか

【出力フォーマット】
以下のJSONフォーマットのみで回答してください。JSON以外のテキストは一切出力しないでください。
{{
  "status": "PASS" または "FAIL",
  "reason": "合否の理由。FAILの場合は階層設計の不整合がある箇所とその理由",
  "suggestions": "不整合を解消するための具体的なドキュメント修正案 (Markdown)"
}}
"""
    raw_response = call_llm(prompt, args)
    return parse_llm_json_response(raw_response)


def perform_section_hierarchy_check(parent_heading: str, parent_body: str, parent_keywords: list[str],
                                     child_heading: str, child_body: str, child_keywords: list[str],
                                     confidence: float, args) -> dict:
    """LLM Check for a single section pair in hierarchy mode."""
    parent_body_preview = parent_body[:1500] if parent_body else "(empty)"
    child_body_preview = child_body[:1500] if child_body else "(empty)"

    prompt = f"""\
あなたはFireballシステムの仕様書査読スペシャリストです。
親レイヤーと子レイヤーの対応するセクションペアについて、詳細なレビューポイントを生成してください。

【親レイヤーセクション】
見出し: {parent_heading}
キーワード: {', '.join(f'{{{kw}}}' for kw in parent_keywords) if parent_keywords else 'なし'}
本文:
{parent_body_preview}

【子レイヤーセクション】
見出し: {child_heading}
キーワード: {', '.join(f'{{{kw}}}' for kw in child_keywords) if child_keywords else 'なし'}
本文:
{child_body_preview}

【レビュー項目生成】
以下の観点でこのセクションペアをレビューすべき項目を箇条書きで列挙してください。
1. API・インターフェース整合性（引数、戻り値の型・説明）
2. 状態遷移・ライフサイクル（タイミング、プロトコル、所有権移譲）
3. キーワード充足性（親レイヤーの要求がすべて実装されているか）
4. エラーハンドリング・リカバリ戦略
5. メモリ制約・パフォーマンス非機能要求

【出力フォーマット】
以下のJSONのみで回答してください。JSON以外は出力しないでください。
{{
  "status": "PASS" または "FAIL",
  "reason": "PASSの場合は『セクション間の整合性が確保されている』旨、FAILの場合は具体的な不整合箇所と理由",
  "review_points": [
    "レビューポイント1: 具体的な検証項目...",
    "レビューポイント2: ...",
    ...
  ],
  "risk_level": "高" | "中" | "低",
  "suggestions": "改善提案（Markdown形式）"
}}
"""

    try:
        raw_response = call_llm(prompt, args)
        result = parse_llm_json_response(raw_response)
        # Ensure required fields exist
        if "status" not in result:
            result["status"] = "ERROR"
        if "risk_level" not in result:
            result["risk_level"] = "不明"
        if "review_points" not in result:
            result["review_points"] = []
        return result
    except Exception as e:
        return {
            "status": "ERROR",
            "reason": f"LLM execution error: {e}",
            "review_points": [],
            "risk_level": "不明",
            "suggestions": ""
        }


def resolve_hierarchy_docs(tier: int) -> list[tuple[Path, Path]]:
    """Resolves and returns list of (parent_path, child_path) pairs for hierarchy check based on Tier."""
    pairs = []
    
    tier_dirs = {
        0: [DOCS_DIR / "requires"],
        1: [COMPONENTS_DIR / "core", COMPONENTS_DIR / "interface"],
        2: [COMPONENTS_DIR / "runtime", COMPONENTS_DIR / "jit"],
        3: [COMPONENTS_DIR / "platform"]
    }
    
    IGNORE_KEYWORDS = load_meta_keywords()

    
    def gather_md_files(dirs: list[Path]) -> list[Path]:
        files = []
        for d in dirs:
            if d.exists():
                files.extend(list(d.glob("**/*.md")))
        return [f for f in files if f.name not in ["FORMAT.md", "CHECKLIST.md", "consistency_checklist.csv", "spec_matrix.csv", "traceability_matrix.csv"]]

    if tier == 1:
        parent_file = REQUIREMENT_FILE
        if not parent_file.exists():
            log_warn(f"Parent requirement file not found: {parent_file}")
            return []
        children = gather_md_files(tier_dirs[1])
        for child in children:
            pairs.append((parent_file, child))
            
    elif tier in [2, 3]:
        parents = gather_md_files(tier_dirs[tier - 1])
        children = gather_md_files(tier_dirs[tier])
        
        for child in children:
            try:
                with open(child, "r", encoding="utf-8") as f:
                    child_content = f.read()
            except Exception as e:
                log_error(f"Failed to read child file {child}: {e}")
                continue
                
            child_kws = set(KEYWORD_PATTERN.findall(child_content)) - IGNORE_KEYWORDS
            
            matched_parents = []
            for parent in parents:
                try:
                    with open(parent, "r", encoding="utf-8") as f:
                        parent_content = f.read()
                except Exception as e:
                    log_error(f"Failed to read parent file {parent}: {e}")
                    continue
                
                parent_kws = set(KEYWORD_PATTERN.findall(parent_content)) - IGNORE_KEYWORDS
                shared = child_kws.intersection(parent_kws)
                if shared:
                    matched_parents.append(parent)
                else:
                    # Also match by stem if the parent file's stem is referenced in child's text or vice-versa
                    if parent.stem in child_content or child.stem in parent_content:
                        matched_parents.append(parent)
            
            if not matched_parents:
                # Target fallback: select major definitions only to avoid huge matrix of unrelated checks
                fallback_names = []
                if tier == 2:
                    fallback_names = ["os_coos.md", "system_config.md"]
                elif tier == 3:
                    fallback_names = ["runtime_vsoc.md", "runtime_interpreter.md"]
                
                matched_parents = [p for p in parents if p.name in fallback_names]
                if not matched_parents:
                    matched_parents = parents[:2]
                
            for parent in matched_parents:
                pairs.append((parent, child))
                
    return pairs


def print_rich_report(results: list[dict]):
    """Prints a beautiful summary report using the rich library."""
    console.print("\n[bold cyan]==================================================[/bold cyan]")
    console.print("[bold cyan]       Fireball Documentation LLM Audit Report     [/bold cyan]")
    console.print("[bold cyan]==================================================[/bold cyan]\n")

    total_failures = 0

    for res in results:
        filepath = res["file"]
        mode = res.get("mode", "MODULE")
        console.print(f"[bold underline]Target: {filepath} [{mode} Mode][/bold underline]")
        if "keywords" in res and res["keywords"]:
            console.print(f"[dim]Keywords: {', '.join(res['keywords'])}[/dim]")

        if "error" in res:
            console.print(f"  [bold red]ERROR:[/bold red] {res['error']}\n")
            total_failures += 1
            continue

        checks = res["checks"]
        if "error" in checks:
            console.print(f"  [bold red]ERROR during run:[/bold red] {checks['error']}\n")
            total_failures += 1
            continue

        check_categories = []
        if mode == "MODULE":
            check_categories = [
                ("policy", "1. 開発方針・メモリ制限適合性"),
                ("traceability", "2. 要求トレーサビリティ充足性"),
                ("quality", "3. ドキュメント品質・曖昧さ検証")
            ]
        elif mode == "PAIR":
            check_categories = [
                ("combination", "1. 組み合わせ境界整合性検証")
            ]
        elif mode == "HIERARCHY":
            check_categories = [
                ("hierarchy", "1. 階層的カプセル化・抽象化検証")
            ]

        for check_key, check_title in check_categories:
            chk = checks.get(check_key, {})
            status = chk.get("status", "ERROR")
            reason = chk.get("reason", "N/A")
            suggestions = chk.get("suggestions", "")
            risk_level = chk.get("risk_level", "")
            review_points = chk.get("review_points", [])
            confidence = res.get("confidence", None)

            # Status indicator
            if status == "PASS":
                console.print(f"  [bold green]✓ PASS[/bold green]  {check_title}")
            elif status == "INCOMPLETE":
                total_failures += 1
                console.print(f"  [bold yellow]⚠ INCOMPLETE[/bold yellow]  {check_title}")
                console.print(f"        {reason}")
            elif status == "FAIL":
                total_failures += 1
                console.print(f"  [bold red]✗ FAIL[/bold red]  {check_title}")
                console.print(f"        {reason}")
                if suggestions:
                    console.print(f"        [bold yellow]改善案:[/bold yellow]\n{suggestions}")
            else:
                total_failures += 1
                console.print(f"  [bold red]ERROR[/bold red]  {check_title}: {reason}")

            # Section-matrix specific details
            if mode == "HIERARCHY" and confidence is not None:
                console.print(f"        [dim]整合度: {confidence:.0%}[/dim]", end="")
                if risk_level and risk_level != "不明":
                    risk_color = "red" if risk_level == "高" else "yellow" if risk_level == "中" else "green"
                    console.print(f"  リスク: [{risk_color}]{risk_level}[/{risk_color}]")
                else:
                    console.print()

            # Review points
            if review_points and isinstance(review_points, list):
                console.print("        [bold]検証項目:[/bold]")
                for point in review_points[:3]:  # Show first 3 points to avoid clutter
                    console.print(f"          • {point}")
                if len(review_points) > 3:
                    console.print(f"          ... ({len(review_points) - 3} more)")

        console.print()

    table = Table(title="監査結果サマリー (Audit Summary)")
    table.add_column("監査対象 (Target)", style="cyan")
    table.add_column("モード (Mode)", style="magenta")
    table.add_column("結果 (Result)", style="bold")

    for res in results:
        filepath = res["file"]
        mode = res.get("mode", "MODULE")
        if "error" in res or "error" in res.get("checks", {}):
            table.add_row(filepath, mode, "[red]FAIL (Error)[/red]")
            continue

        checks = res["checks"]
        all_pass = True
        for k, v in checks.items():
            if v.get("status") != "PASS":
                all_pass = False
                break
        
        row_res = "[green]PASS[/green]" if all_pass else "[red]FAIL[/red]"
        table.add_row(filepath, mode, row_res)

    console.print(table)
    if total_failures > 0:
        console.print(f"\n[bold red]✖ 監査失敗: 合計 {total_failures} 個のチェックで問題が検出されました。[/bold red]")
        sys.exit(1)
    else:
        console.print("\n[bold green]✔ 全てのドキュメント監査に合格しました！[/bold green]")
        sys.exit(0)


def print_plain_report(results: list[dict]):
    """Fallback plain text report if rich is not available."""
    print("\n" + "="*50)
    print("       Fireball Documentation LLM Audit Report     ")
    print("="*50 + "\n")

    total_failures = 0
    for res in results:
        filepath = res["file"]
        mode = res.get("mode", "MODULE")
        print(f"Target: {filepath} [{mode} Mode]")

        if "error" in res:
            print(f"  ERROR: {res['error']}\n")
            total_failures += 1
            continue

        checks = res["checks"]
        if "error" in checks:
            print(f"  ERROR during run: {checks['error']}\n")
            total_failures += 1
            continue

        check_categories = []
        if mode == "MODULE":
            check_categories = [
                ("policy", "1. 開発方針・メモリ制限適合性"),
                ("traceability", "2. 要求トレーサビリティ充足性"),
                ("quality", "3. ドキュメント品質・曖昧さ検証")
            ]
        elif mode == "PAIR":
            check_categories = [
                ("combination", "1. 組み合わせ境界整合性検証")
            ]
        elif mode == "HIERARCHY":
            check_categories = [
                ("hierarchy", "1. 階層的カプセル化・抽象化検証")
            ]

        for check_key, check_title in check_categories:
            chk = checks.get(check_key, {})
            status = chk.get("status", "ERROR")
            reason = chk.get("reason", "N/A")
            suggestions = chk.get("suggestions", "")
            risk_level = chk.get("risk_level", "")
            review_points = chk.get("review_points", [])
            confidence = res.get("confidence", None)

            if status == "PASS":
                print(f"  ✓ PASS  {check_title}")
            elif status == "INCOMPLETE":
                total_failures += 1
                print(f"  ⚠ INCOMPLETE  {check_title}")
                print(f"        {reason}")
            elif status == "FAIL":
                total_failures += 1
                print(f"  ✗ FAIL  {check_title}")
                print(f"        理由: {reason}")
                if suggestions:
                    print(f"        改善案:\n{suggestions}")
            else:
                total_failures += 1
                print(f"  ERROR  {check_title}: {reason}")

            # Section-matrix specific details
            if mode == "HIERARCHY" and confidence is not None:
                print(f"        整合度: {confidence:.0%}", end="")
                if risk_level and risk_level != "不明":
                    print(f"  リスク: {risk_level}")
                else:
                    print()

            if review_points and isinstance(review_points, list):
                print("        検証項目:")
                for point in review_points[:3]:
                    print(f"          • {point}")
                if len(review_points) > 3:
                    print(f"          ... ({len(review_points) - 3} more)")

        print()

    print("-"*50)
    print(f"Total failures/issues: {total_failures}")
    if total_failures > 0:
        print("\n✖ 監査失敗: 問題が検出されました。")
        sys.exit(1)
    else:
        print("\n✔ 全てのドキュメント監査に合格しました！")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Fireball Documentation LLM Auto-Tester")
    
    # Mutually exclusive audit modes
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--module", type=str, help="Audit a specific markdown file for policies, quality, and traceability")
    mode_group.add_argument("--all", action="store_true", help="Audit all component specifications for policies, quality, and traceability")
    mode_group.add_argument("--pair", type=str, nargs=2, metavar=("FILE_A", "FILE_B"), help="Audit dynamic consistency and boundaries between two files")
    mode_group.add_argument("--hierarchy", action="store_true", help="Audit abstraction levels and trace validation across tiers")

    # Additional options
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], help="Tier to audit when using --hierarchy (1: Req vs Core, 2: Core vs Runtime, 3: Runtime vs Platform)")
    parser.add_argument("--model", type=str, help="Override LLM model name")
    parser.add_argument("--backend", choices=["sakura", "openrouter", "gemini", "ollama"], help="Force LLM backend")
    parser.add_argument("--max-tokens", type=int, default=1024, help="Max tokens in LLM generation")

    args = parser.parse_args()

    if args.hierarchy and args.tier is None:
        parser.error("--hierarchy requires --tier <1|2|3> to be specified.")

    results = []

    # 1. Module mode
    if args.module or args.all:
        files_to_test = []
        if args.module:
            p = Path(args.module).resolve()
            if not p.exists():
                log_error(f"Specified file does not exist: {args.module}")
                sys.exit(1)
            files_to_test.append(p)
        elif args.all:
            if not COMPONENTS_DIR.exists():
                log_error(f"Components directory not found at {COMPONENTS_DIR}")
                sys.exit(1)
            files_to_test = sorted(list(COMPONENTS_DIR.glob("**/*.md")))
            
        # Filter out meta files
        files_to_test = [f for f in files_to_test if f.name not in ["FORMAT.md", "CHECKLIST.md", "consistency_checklist.csv", "spec_matrix.csv", "traceability_matrix.csv"]]

        if not files_to_test:
            log_warn("No markdown files found to test.")
            sys.exit(0)

        log_info(f"Loaded {len(files_to_test)} markdown files for module audit.")
        policies = load_project_policies()
        kw_defs = load_requirement_keywords()
        log_info(f"Loaded {len(policies)} policy files and {len(kw_defs)} requirement definitions.")

        for idx, filepath in enumerate(files_to_test, start=1):
            relpath = filepath.relative_to(REPO_ROOT)
            log_info(f"[{idx}/{len(files_to_test)}] Auditing {relpath}...")
            res = run_checks_on_file(filepath, policies, kw_defs, args)
            res["mode"] = "MODULE"
            results.append(res)

    # 2. Pair mode
    elif args.pair:
        file_a = Path(args.pair[0]).resolve()
        file_b = Path(args.pair[1]).resolve()
        
        if not file_a.exists():
            log_error(f"Pair file A does not exist: {args.pair[0]}")
            sys.exit(1)
        if not file_b.exists():
            log_error(f"Pair file B does not exist: {args.pair[1]}")
            sys.exit(1)

        log_info(f"Auditing boundary consistency between {file_a.name} and {file_b.name}...")
        try:
            with open(file_a, "r", encoding="utf-8") as f:
                content_a = f.read()
            with open(file_b, "r", encoding="utf-8") as f:
                content_b = f.read()
            chk_res = perform_pair_check(file_a, content_a, file_b, content_b, args)
        except Exception as e:
            chk_res = {"status": "ERROR", "reason": f"Execution error: {e}", "suggestions": ""}
        
        results.append({
            "file": f"{file_a.name} x {file_b.name}",
            "mode": "PAIR",
            "checks": {"combination": chk_res}
        })

    # 3. Hierarchy mode
    elif args.hierarchy:
        pairs = resolve_hierarchy_docs(args.tier)
        if not pairs:
            log_warn(f"No parent-child document pairs resolved for Tier {args.tier}.")
            sys.exit(0)

        log_info(f"Loaded {len(pairs)} parent-child pairs for Tier {args.tier} audit.")

        # Section-based matrix review if available
        if SECTION_MATRIX_AVAILABLE:
            log_info("Using section-based hierarchy audit (fine-grained analysis)")
            pair_idx = 0
            for parent, child in pairs:
                pair_idx += 1
                log_info(f"[{pair_idx}/{len(pairs)}] Processing {parent.name} (Parent) x {child.name} (Child)...")

                try:
                    parent_sections = extract_sections_from_file(parent)
                    child_sections = extract_sections_from_file(child)
                    section_pairs = match_sections(parent_sections, child_sections)

                    log_info(f"  Found {len(section_pairs)} section pairs to review")

                    for sec_idx, (parent_sec, child_sec, confidence) in enumerate(section_pairs, start=1):
                        if parent_sec is None or child_sec is None:
                            # Unmatched section
                            parent_heading = parent_sec.heading if parent_sec else "(no match)"
                            child_heading = child_sec.heading if child_sec else "(no match)"
                            sec_res = {
                                "status": "INCOMPLETE",
                                "reason": "Section correspondence not found",
                                "review_points": [],
                                "risk_level": "高",
                                "suggestions": "Design gap detected - section in one layer has no counterpart in the other"
                            }
                        else:
                            # Regular section pair review
                            log_info(f"    [{sec_idx}] Reviewing: {parent_sec.heading} → {child_sec.heading}")
                            sec_res = perform_section_hierarchy_check(
                                parent_sec.heading, parent_sec.body, parent_sec.keywords,
                                child_sec.heading, child_sec.body, child_sec.keywords,
                                confidence, args
                            )

                        parent_heading = parent_sec.heading if parent_sec else "(no match)"
                        child_heading = child_sec.heading if child_sec else "(no match)"

                        results.append({
                            "file": f"{parent.name} § {parent_heading} → {child.name} § {child_heading}",
                            "mode": "HIERARCHY",
                            "parent_file": parent.name,
                            "child_file": child.name,
                            "confidence": confidence,
                            "checks": {"hierarchy": sec_res}
                        })
                except Exception as e:
                    log_error(f"Section extraction failed for pair {pair_idx}: {e}")
                    results.append({
                        "file": f"{parent.name} (Parent) x {child.name} (Child)",
                        "mode": "HIERARCHY",
                        "checks": {"hierarchy": {"status": "ERROR", "reason": f"Extraction error: {e}", "suggestions": ""}}
                    })
        else:
            # Fallback to file-level audit (legacy mode)
            log_warn("Section matrix support not available, falling back to file-level audit")
            for idx, (parent, child) in enumerate(pairs, start=1):
                log_info(f"[{idx}/{len(pairs)}] Auditing {parent.name} (Parent) x {child.name} (Child)...")
                try:
                    with open(parent, "r", encoding="utf-8") as f:
                        p_content = f.read()
                    with open(child, "r", encoding="utf-8") as f:
                        c_content = f.read()
                    h_res = perform_hierarchy_check(parent, p_content, child, c_content, args)
                except Exception as e:
                    h_res = {"status": "ERROR", "reason": f"Execution error: {e}", "suggestions": ""}

                results.append({
                    "file": f"{parent.name} (Parent) x {child.name} (Child)",
                    "mode": "HIERARCHY",
                    "checks": {"hierarchy": h_res}
                })

    # 4. Report results
    if USE_RICH:
        print_rich_report(results)
    else:
        print_plain_report(results)


if __name__ == "__main__":
    main()
