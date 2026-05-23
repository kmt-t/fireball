import csv
import json
import re
import textwrap
from pathlib import Path
from tools.common.db import db
from tools.common.llm import call_llm, parse_llm_json_response
from tools.common.parser import parse_md_tokens, heading_text, token_text, extract_sections_by_headers

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPONENTS_DIR = REPO_ROOT / "docs" / "components"
CHECKLIST_CSV = REPO_ROOT / "temp" / "consistency_checklist.csv"
REQUIREMENT_FILE = REPO_ROOT / "docs" / "requires" / "requirement_list.md"

CHECKLIST_FIELDS = [
    'pair_id', 'file_a', 'file_b', 'shared_keywords',
    'file_a_section', 'file_b_section',
    'check_num', 'aspect', 'check_content',
    'llm_result', 'llm_reason',
]

def audit_pair_files(file_a: Path, file_b: Path, backend: str = None, model: str = None, max_tokens: int = 2048) -> dict:
    """Directly audit pairwise consistency (S-ARCH-PAIR) between two files."""
    try:
        content_a = file_a.read_text(encoding="utf-8")
        content_b = file_b.read_text(encoding="utf-8")
    except Exception as e:
        return {"status": "ERROR", "reason": f"Failed to read files: {e}"}

    model_name = model or "default"
    backend_name = backend or "auto"
    input_hash = db.make_hash_key(content_a, content_b, model_name, backend_name)
    
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

【検証項目 (S-ARCH-PAIR)】
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
    raw_response = call_llm(prompt, backend=backend, model=model, max_tokens=max_tokens)
    res = parse_llm_json_response(raw_response)

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

def _find_sections_with_keyword(text: str, keyword: str) -> list[str]:
    tokens = parse_md_tokens(text)
    current_header = "(ファイル先頭)"
    found = []
    target = f"{{{keyword}}}"

    for token in tokens:
        if token.get("type") == "heading":
            level = token.get("attrs", {}).get("level", 0)
            if level <= 3:
                current_header = heading_text(token)
        elif target in token_text(token) and current_header not in found:
            found.append(current_header)

    return found

def _build_keyword_section_map(all_files: list[str], file_kw_map: dict[str, set[str]]) -> dict[str, list[dict]]:
    kw_map = {}
    for fp in all_files:
        file_path = REPO_ROOT / fp
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8")
        for kw in file_kw_map.get(fp, set()):
            headers = _find_sections_with_keyword(text, kw)
            if not headers:
                continue
            if kw not in kw_map:
                kw_map[kw] = []
            kw_map[kw].append({"file": fp, "sections": headers})
    return {kw: mentions for kw, mentions in kw_map.items() if len(mentions) >= 2}

def _extract_keyword_definitions(req_text: str) -> dict[str, str]:
    definitions = {}
    pattern = re.compile(r'^\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|\s*([^|]+?)\s*\|')
    for line in req_text.splitlines():
        m = pattern.match(line.strip())
        if m:
            definitions[m.group(1)] = m.group(2).strip()
    return definitions

def generate_checklist(all_files: list[str], file_kw_map: dict[str, set[str]], backend: str = None, model: str = None, max_tokens: int = 8192) -> list[dict]:
    """Generates consistency checklist based on spec matrix keyword overlaps."""
    checklist_path = COMPONENTS_DIR / "CHECKLIST.md"
    matrix_path = COMPONENTS_DIR / "CONSISTENCY_MATRIX.md"

    checklist_text = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else ""
    aspect_text = ""
    if matrix_path.exists():
        aspect_text = extract_sections_by_headers(
            matrix_path.read_text(encoding="utf-8"), ["観点"], max_chars=1200
        )

    kw_definitions = {}
    if REQUIREMENT_FILE.exists():
        kw_definitions.update(_extract_keyword_definitions(REQUIREMENT_FILE.read_text(encoding="utf-8")))
    doc_struct = REPO_ROOT / "docs" / "architecture" / "document_structure.md"
    if doc_struct.exists():
        kw_definitions.update(_extract_keyword_definitions(doc_struct.read_text(encoding="utf-8")))

    kw_section_map = _build_keyword_section_map(all_files, file_kw_map)
    name_to_path = {Path(fp).name: fp for fp in all_files}

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

    raw_response = call_llm(prompt, backend=backend, model=model, max_tokens=max_tokens)
    result = parse_llm_json_response(raw_response)

    if "error" in result or result.get("status") == "ERROR":
        print(f"  [LLM] チェックリスト生成失敗: {result.get('error', result)}")
        return []

    items = []
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

def save_csv_checklist(items: list[dict]):
    CHECKLIST_CSV.parent.mkdir(parents=True, exist_ok=True)
    with CHECKLIST_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CHECKLIST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)
    db.update_consistency_checklist(items)

def read_csv_checklist() -> list[dict]:
    if not CHECKLIST_CSV.exists():
        return []
    with CHECKLIST_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)

def _llm_check(pair_id: str, label: str, excerpt_a: str, excerpt_b: str,
               items: list[tuple[str, str]], backend: str = None, model: str = None) -> dict:
    items_text = "\n".join(f"- [{item_id}] {desc}" for item_id, desc in items)
    prompt = textwrap.dedent("""\
    あなたはFireballプロジェクトの仕様書整合性チェッカーです。
    2つの仕様書の抜粋を比較し、指定された観点で整合性を判定してください。

    【出力ルール】
    - 以下のJSON形式のみで回答すること。説明文・前置き・コードブロックは不要。
    - statusは PASS（整合）, FAIL（矛盾あり）, WARN（記述不足/判断不能）のいずれか。

    出力例:
    {"items":[{"id":"1","status":"PASS","reason":"両方の文書で同じ値を使用"},{"id":"2","status":"FAIL","reason":"Aでは5KB、Bでは8KBと記述が異なる"}],"summary":"FAIL"}

    """) + textwrap.dedent(f"""\
        ## チェック対象: {pair_id} - {label}

        ### 仕様書 A の抜粋
        {excerpt_a}

        ### 仕様書 B の抜粋
        {excerpt_b}

        ### チェック項目（各項目を判定してください）
        {items_text}

        上記を根拠として、各チェック項目のstatusとreasonを含むJSONのみを出力してください。
    """)

    raw = call_llm(prompt, backend=backend, model=model, max_tokens=2048)
    return parse_llm_json_response(raw)

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
                    clean = h.lstrip("#").strip()
                    if clean:
                        hints.append(clean)
            return list(dict.fromkeys(hints))

        a_hints = _get_hints(pitems, "file_a_section")
        b_hints = _get_hints(pitems, "file_b_section")
        excerpt_a = extract_sections_by_headers(text_a, a_hints, max_chars=3000)
        excerpt_b = extract_sections_by_headers(text_b, b_hints, max_chars=3000)

        label = f"{Path(file_a_rel).name} × {Path(file_b_rel).name}"
        result = _llm_check(pid, label, excerpt_a, excerpt_b, [(i["check_num"], i["check_content"]) for i in pitems], backend=backend, model=model)
        
        summary = result.get("summary", "FAIL")
        if summary != "PASS":
            total_failures += 1

        print(f"  [{pid}: {label}] -> {summary}")
        rmap = {r.get("id", ""): r for r in result.get("items", [])}
        for item in pitems:
            r = rmap.get(item["check_num"])
            if r:
                item["llm_result"] = r.get("status", "FAIL")
                item["llm_reason"] = r.get("reason", "No reason provided")
                print(f"    - [{item['check_num']}] {item['llm_result']} {item['llm_reason']}")

    save_csv_checklist(items)
    return total_failures
