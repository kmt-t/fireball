import json
import re
import textwrap
from pathlib import Path
from tools.common.db import db
from tools.common.llm import OLLAMA_NUM_CTX, call_llm, parse_llm_markdown_response
from tools.common.parser import extract_sections_by_headers
from tools.mechanical.check_consistency import (
    CHECKLIST_CSV,
    CHECKLIST_FIELDS,
    read_csv_checklist,
    save_csv_checklist,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPONENTS_DIR = REPO_ROOT / "docs" / "components"
REQUIREMENT_FILE = REPO_ROOT / "docs" / "requires" / "requirement_list.md"

def generation_cache_key(model_name: str, backend_name: str, max_tokens: int) -> str:
    return f"{backend_name}:{model_name}:ctx={OLLAMA_NUM_CTX}:tokens={max_tokens}:fmt=md2"

def audit_pair_files(file_a: Path, file_b: Path, backend: str = None, model: str = None, max_tokens: int = 2048) -> dict:
    """Directly audit pairwise consistency (S-ARCH-PAIR) between two files."""
    try:
        content_a = file_a.read_text(encoding="utf-8")
        content_b = file_b.read_text(encoding="utf-8")
    except Exception as e:
        return {"status": "ERROR", "reason": f"Failed to read files: {e}"}

    model_name = model or "default"
    backend_name = backend or "auto"
    generation_key = generation_cache_key(model_name, backend_name, max_tokens)
    input_hash = db.make_hash_key(content_a, content_b, generation_key)
    
    file_a_rel = str(file_a.relative_to(REPO_ROOT))
    file_b_rel = str(file_b.relative_to(REPO_ROOT))
    hash_key = db.make_hash_key("S-ARCH-PAIR", file_a_rel, file_b_rel, input_hash)

    cached = db.get_cache(hash_key)
    if cached is not None:
        return cached

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

    【判定方針】
    これは「同一性の確認」ではなく、「一つのテーマについて、異なる粒度の説明が一貫した物語になっているか」の監査です。
    - 役割分担、依存方向、制約、例外処理の流れが整っていれば PASS。
    - 階層ラベル、語彙の違い、説明粒度の差だけでは FAIL にしない。
    - 同じ対象について、数値、型、所有権、タイミング、禁止事項、責務が食い違う場合のみ FAIL。
    - 片方の文書だけでは十分な根拠がなく断定しづらい場合は WARN。

    【検証項目 (S-ARCH-PAIR)】
    1. テーマ一貫性: 両文書が同じ設計テーマを別の抽象度で説明しているか
    2. 責務と境界: 役割分担や責務の線引きに矛盾がないか
    3. 具体仕様の衝突: 型、数値、タイミング、所有権、禁止事項に直接の衝突がないか
    4. 記述不足: 片方の記述が薄く、関係性を判断しづらいだけのケースを FAIL にしていないか

    【出力フォーマット】
    以下のMarkdown形式のみで回答してください。JSON、コードブロック、前置きは出力しないでください。

    STATUS: PASS または FAIL または WARN
    REASON: 判定の具体的な根拠。FAILの場合はどのテーマ/責務/仕様が衝突しているかを書く。WARNの場合は判断不能の理由を書く。
    SUGGESTIONS: 不整合または記述不足を解消するための具体的なドキュメント修正案
    """
    raw_response = call_llm(prompt, backend=backend, model=model, max_tokens=max_tokens)
    res = parse_llm_markdown_response(raw_response)

    if res.get("status") in ["PASS", "FAIL", "UNCERTAIN"]:
        db.set_cache(
            hash_key,
            rule_code="S-ARCH-PAIR",
            target_type="pair",
            file_path=file_a_rel,
            heading=file_b_rel,
            status=res["status"],
            reason=res.get("reason", ""),
            suggestions=res.get("suggestions", ""),
            input_hash=input_hash
        )
    return res

def _parse_checklist_markdown(raw: str) -> dict:
    def _normalize_status(value: str) -> str:
        normalized = value.strip().upper()
        if normalized == "WARN":
            return "UNCERTAIN"
        if normalized in {"PASS", "FAIL", "UNCERTAIN"}:
            return normalized
        return ""

    def _is_structural_line(text: str) -> bool:
        return bool(re.match(
            r"^(?:#{1,6}\s*)?(?:SUMMARY|ITEMS|ID|STATUS|REASON|SUGGESTIONS)\b",
            text,
            re.IGNORECASE,
        ))

    summary = "FAIL"
    summary_pending = False
    items = []
    current = None
    current_field = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            continue

        m_summary = re.match(r"^(?:#{1,6}\s*)?SUMMARY(?:\s*[:：]\s*(.*))?$", stripped, re.IGNORECASE)
        if m_summary:
            if current is not None:
                items.append(current)
            current = None
            current_field = None
            summary_pending = True
            inline = m_summary.group(1)
            if inline:
                normalized = _normalize_status(inline)
                if normalized:
                    summary = normalized
                    summary_pending = False
            continue

        if summary_pending:
            normalized = _normalize_status(stripped)
            if normalized:
                summary = normalized
                summary_pending = False
                continue

        if re.match(r"^(?:#{1,6}\s*)?ITEMS(?:\s*[:：].*)?$", stripped, re.IGNORECASE):
            continue

        m_id = re.match(
            r"^(?:[-*+]\s*|\d+[.)]\s*)?(?:#{1,6}\s*)?ID\s*[:：]\s*(.+)$",
            stripped,
            re.IGNORECASE,
        )
        if m_id:
            if current is not None:
                items.append(current)
            current = {"id": m_id.group(1).strip(), "status": "FAIL", "reason": ""}
            current_field = None
            continue

        m_field = re.match(
            r"^(?:#{1,6}\s*)?(STATUS|REASON|SUGGESTIONS)\s*[:：]?\s*(.*)$",
            stripped,
            re.IGNORECASE,
        )
        if m_field:
            field = m_field.group(1).lower()
            value = m_field.group(2).strip()

            # Top-level STATUS is accepted as a summary fallback.
            if current is None:
                if field == "status":
                    if value:
                        normalized = _normalize_status(value)
                        if normalized:
                            summary = normalized
                            summary_pending = False
                    else:
                        summary_pending = True
                continue

            current_field = field if field in {"reason", "suggestions"} else None
            if field == "status":
                if value:
                    normalized = _normalize_status(value)
                    if normalized:
                        current["status"] = normalized
                else:
                    current_field = "status"
                continue

            if value:
                current[field] = value
            continue

        if current is not None and current_field == "status":
            normalized = _normalize_status(stripped)
            if normalized:
                current["status"] = normalized
                current_field = None
                continue

        if current is not None and current_field in {"reason", "suggestions"} and not _is_structural_line(stripped):
            current[current_field] = (current[current_field] + "\n" + stripped).strip()

    if current is not None:
        items.append(current)

    return {"items": items, "summary": summary}


def _llm_check(pair_id: str, label: str, excerpt_a: str, excerpt_b: str,
               items: list[tuple[str, str, str]], backend: str = None, model: str = None) -> dict:
    items_text = "\n".join(f"- [{item_id}] ({aspect}) {desc}" for item_id, aspect, desc in items)
    prompt = textwrap.dedent("""\
    あなたはFireballプロジェクトの仕様書整合性チェッカーです。
    2つの仕様書の抜粋を比較し、指定された観点で「同一性」ではなく「テーマと責務の一貫性」を判定してください。
    階層や役割が異なること自体は失敗条件ではありません。役割分担・依存方向・制約・例外の扱いが一貫しているかを見てください。

【出力ルール】
    - 以下のMarkdown形式のみで回答すること。JSON、コードブロック、前置きは出力しないでください。
    - statusは PASS（整合）, FAIL（矛盾あり）, WARN（記述不足/判断不能）のいずれか。
    - 余計な見出しや補足文は追加しないでください。`SUMMARY` / `ITEMS` / `ID` / `STATUS` / `REASON` / `SUGGESTIONS` だけを使ってください。
    - `FAIL` は明示的な衝突がある場合のみ。単なる詳細不足や抽象度差は `WARN` にしてください。

    SUMMARY: PASS または FAIL または WARN
    ITEMS:
    - ID: 1
      STATUS: PASS
      REASON: 両方の文書で同じテーマを別粒度で説明しており、役割分担に矛盾がない
    - ID: 2
      STATUS: FAIL
      REASON: Aは5KB、Bは8KBとし、同じ対象の制約値が衝突している

    """) + textwrap.dedent(f"""\
        ## チェック対象: {pair_id} - {label}

        ### 仕様書 A の抜粋
        {excerpt_a}

        ### 仕様書 B の抜粋
        {excerpt_b}

        ### チェック項目（各項目を判定してください）
        {items_text}

        上記を根拠として、各チェック項目のID、STATUS、REASON、必要に応じてSUGGESTIONSを含むMarkdownのみを出力してください。
    """)

    raw = call_llm(prompt, backend=backend, model=model, max_tokens=2048, apply_contract=False)
    parsed = _parse_checklist_markdown(raw)
    if parsed.get("items"):
        return parsed
    return {"items": [], "summary": "FAIL", "summary_reason": "Failed to parse LLM Markdown response."}

def run_checklist_audit(items: list[dict], backend: str = None, model: str = None) -> int:
    pairs = {}
    for item in items:
        pairs.setdefault(item["pair_id"], []).append(item)

    total_failures = 0
    for pid, pitems in pairs.items():
        file_a_rel = pitems[0].get("file_a", "")
        file_b_rel = pitems[0].get("file_b", "")

        file_a_path = REPO_ROOT / file_a_rel
        file_b_path = REPO_ROOT / file_b_rel

        if not file_a_path.exists() or not file_b_path.exists():
            print(f"  [ERROR] File not found: {file_a_rel} or {file_b_rel}")
            total_failures += 1
            continue

        text_a = file_a_path.read_text(encoding="utf-8")
        text_b = file_b_path.read_text(encoding="utf-8")

        def _get_hints(items_list: list[dict], key: str) -> list[str]:
            hints = []
            for it in items_list:
                h = it.get(key)
                if h:
                    parts = h.split("|")
                    for part in parts:
                        clean = part.lstrip("#").strip()
                        if clean:
                            hints.append(clean)
            return list(dict.fromkeys(hints))

        a_hints = _get_hints(pitems, "file_a_section")
        b_hints = _get_hints(pitems, "file_b_section")
        excerpt_a = extract_sections_by_headers(text_a, a_hints, max_chars=3000)
        excerpt_b = extract_sections_by_headers(text_b, b_hints, max_chars=3000)

        label = f"{Path(file_a_rel).name} × {Path(file_b_rel).name}"

        model_name = model or "default"
        backend_name = backend or "auto"
        pitems_json = json.dumps(
            [(i["check_num"], i["aspect"], i["check_content"], i["file_a_section"], i["file_b_section"]) for i in pitems],
            sort_keys=True
        )
        generation_key = generation_cache_key(model_name, backend_name, 2048)
        input_hash = db.make_hash_key(excerpt_a, excerpt_b, pitems_json, generation_key)
        hash_key = db.make_hash_key("S-ARCH-CHECKLIST", file_a_rel, file_b_rel, input_hash)

        cached = db.get_cache(hash_key)
        if cached is not None:
            print(f"  [{pid}: {label}] -> Cached ({cached['status']})")
            try:
                cached_items = json.loads(cached["suggestions"])
                rmap = {r.get("id", ""): r for r in cached_items.get("items", [])}
                for item in pitems:
                    r = rmap.get(item["check_num"])
                    if r:
                        item["llm_result"] = r.get("status", "FAIL")
                        item["llm_reason"] = r.get("reason", "No reason provided")
                        print(f"    - [{item['check_num']}] {item['llm_result']} {item['llm_reason']}")
                if cached["status"] == "FAIL":
                    total_failures += 1
                continue
            except Exception as e:
                print(f"  [Warning] Failed to parse cache for {pid}: {e}. Re-running audit...")

        result = _llm_check(
            pid,
            label,
            excerpt_a,
            excerpt_b,
            [(i["check_num"], i["aspect"], i["check_content"]) for i in pitems],
            backend=backend,
            model=model,
        )
        
        summary = result.get("summary", "FAIL")
        if summary == "FAIL":
            total_failures += 1

        print(f"  [{pid}: {label}] -> {summary}")
        rmap = {r.get("id", ""): r for r in result.get("items", [])}
        for item in pitems:
            r = rmap.get(item["check_num"])
            if r:
                item["llm_result"] = r.get("status", "FAIL")
                item["llm_reason"] = r.get("reason", "No reason provided")
                print(f"    - [{item['check_num']}] {item['llm_result']} {item['llm_reason']}")

        # Cache the result
        suggestions_json = json.dumps({"items": result.get("items", [])})
        db.set_cache(
            hash_key,
            rule_code="S-ARCH-CHECKLIST",
            target_type="checklist_pair",
            file_path=file_a_rel,
            heading=file_b_rel,
            status=summary,
            reason=result.get("summary_reason", f"Summary status: {summary}"),
            suggestions=suggestions_json,
            input_hash=input_hash
        )

    save_csv_checklist(items)
    return total_failures
