import re
from pathlib import Path
from difflib import SequenceMatcher
from tools.common.db import db
from tools.common.llm import call_llm, parse_llm_json_response
from tools.common.parser import parse_sections

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = REPO_ROOT / "docs"
COMPONENTS_DIR = DOCS_DIR / "components"

def resolve_hierarchy_docs(tier: int) -> list[tuple[Path, Path]]:
    pairs = []
    tier_dirs = {
        0: [DOCS_DIR / "requires"],
        1: [COMPONENTS_DIR / "core", COMPONENTS_DIR / "interface"],
        2: [COMPONENTS_DIR / "runtime", COMPONENTS_DIR / "jit"],
        3: [COMPONENTS_DIR / "platform"]
    }
    
    ignore_keywords = db.load_meta_keywords()

    def gather_md_files(dirs: list[Path]) -> list[Path]:
        files = []
        for d in dirs:
            if d.exists():
                files.extend(list(d.glob("**/*.md")))
        return [f for f in files if f.name not in ["FORMAT.md", "CHECKLIST.md", "consistency_checklist.csv", "spec_matrix.csv", "traceability_matrix.csv"]]

    if tier == 1:
        parent_file = DOCS_DIR / "requires" / "requirement_list.md"
        if not parent_file.exists():
            return []
        children = gather_md_files(tier_dirs[1])
        for child in children:
            pairs.append((parent_file, child))
            
    elif tier in [2, 3]:
        parents = gather_md_files(tier_dirs[tier - 1])
        children = gather_md_files(tier_dirs[tier])
        
        TEMPLATE_KW_PATTERN = {"Decision_", "Strategy_", "Requirement_", "req_", "concept", "Constraint_"}
        
        for child in children:
            try:
                child_content = child.read_text(encoding="utf-8")
            except Exception:
                continue
                
            child_kws = set(re.findall(r"\{([A-Za-z0-9_]+)\}", child_content)) - ignore_keywords
            child_kws = {k for k in child_kws if not any(k.startswith(p) for p in TEMPLATE_KW_PATTERN)}
            
            matched_parents = []
            for parent in parents:
                try:
                    parent_content = parent.read_text(encoding="utf-8")
                except Exception:
                    continue
                
                parent_kws = set(re.findall(r"\{([A-Za-z0-9_]+)\}", parent_content)) - ignore_keywords
                parent_kws = {k for k in parent_kws if not any(k.startswith(p) for p in TEMPLATE_KW_PATTERN)}
                shared = child_kws.intersection(parent_kws)
                if shared:
                    matched_parents.append(parent)
                else:
                    if parent.stem in child_content or child.stem in parent_content:
                        matched_parents.append(parent)
            
            if not matched_parents:
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

def string_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def match_sections(parent_sections: list, child_sections: list) -> list[tuple]:
    pairs = []
    matched_children = set()

    for parent in parent_sections:
        parent_kws = set(parent.keywords)
        if not parent_kws:
            continue

        for i, child in enumerate(child_sections):
            if i in matched_children:
                continue
            child_kws = set(child.keywords)
            shared = parent_kws.intersection(child_kws)

            if shared:
                confidence = len(shared) / max(len(parent_kws), len(child_kws))
                pairs.append((parent, child, confidence))
                matched_children.add(i)
                break

    unmatched_parents = [p for p in parent_sections if p not in [pair[0] for pair in pairs]]
    unmatched_children = [child_sections[i] for i in range(len(child_sections)) if i not in matched_children]

    for parent in unmatched_parents:
        best_match = None
        best_sim = 0.5

        for child in unmatched_children:
            sim = string_similarity(parent.heading, child.heading)
            if sim > best_sim:
                best_sim = sim
                best_match = child

        if best_match:
            pairs.append((parent, best_match, best_sim))
            unmatched_children.remove(best_match)

    for parent in unmatched_parents:
        if parent not in [pair[0] for pair in pairs]:
            pairs.append((parent, None, 0.0))

    for child in unmatched_children:
        pairs.append((None, child, 0.0))

    return pairs

def perform_section_hierarchy_check(
    parent_heading: str, parent_body: str, parent_keywords: list[str],
    child_heading: str, child_body: str, child_keywords: list[str],
    confidence: float, parent_file: str, child_file: str,
    backend: str = None, model: str = None, max_tokens: int = 2048
) -> tuple[dict, bool]:
    parent_body_preview = parent_body[:1500] if parent_body else "(empty)"
    child_body_preview = child_body[:1500] if child_body else "(empty)"
    
    model_name = model or "default"
    backend_name = backend or "auto"
    input_hash = db.make_hash_key(parent_heading, parent_body, str(parent_keywords),
                                  child_heading, child_body, str(child_keywords),
                                  confidence, model_name, backend_name)
    
    hash_key = db.make_hash_key("S-ARCH-HIERARCHY", parent_heading, child_heading, input_hash)
    cached = db.get_cache(hash_key)
    if cached is not None:
        if "risk_level" not in cached:
            cached["risk_level"] = "不明"
        if "review_points" not in cached:
            cached["review_points"] = []
        return cached, True

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
        raw_response = call_llm(prompt, backend=backend, model=model, max_tokens=max_tokens)
        result = parse_llm_json_response(raw_response)
        if "status" not in result:
            result["status"] = "ERROR"
        if "risk_level" not in result:
            result["risk_level"] = "不明"
        if "review_points" not in result:
            result["review_points"] = []
            
        if result.get("status") in ["PASS", "FAIL", "UNCERTAIN"]:
            db.set_cache(
                hash_key,
                rule_code="S-ARCH-HIERARCHY",
                target_type="hierarchy_section",
                file_path=parent_file,
                heading=f"{parent_heading} -> {child_heading}",
                status=result["status"],
                reason=result.get("reason", ""),
                suggestions=result.get("suggestions", ""),
                input_hash=input_hash
            )
        return result, False
    except Exception as e:
        return {
            "status": "ERROR",
            "reason": f"LLM execution error: {e}",
            "review_points": [],
            "risk_level": "不明",
            "suggestions": ""
        }, False

def audit_hierarchy_tier(tier: int, backend: str = None, model: str = None, max_tokens: int = 2048) -> list[dict]:
    pairs = resolve_hierarchy_docs(tier)
    results = []
    
    for parent, child in pairs:
        try:
            parent_sections = parse_sections(parent)
            child_sections = parse_sections(child)
            section_pairs = match_sections(parent_sections, child_sections)
            
            for parent_sec, child_sec, confidence in section_pairs:
                if parent_sec is None or child_sec is None:
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
                    parent_heading = parent_sec.heading
                    child_heading = child_sec.heading
                    sec_res, cached_sec = perform_section_hierarchy_check(
                        parent_sec.heading, parent_sec.body, parent_sec.keywords,
                        child_sec.heading, child_sec.body, child_sec.keywords,
                        confidence, parent.name, child.name,
                        backend=backend, model=model, max_tokens=max_tokens
                    )
                
                results.append({
                    "file": f"{parent.name} § {parent_heading} → {child.name} § {child_heading}",
                    "mode": "HIERARCHY",
                    "parent_file": parent.name,
                    "child_file": child.name,
                    "confidence": confidence,
                    "checks": {"hierarchy": sec_res}
                })
        except Exception as e:
            results.append({
                "file": f"{parent.name} (Parent) x {child.name} (Child)",
                "mode": "HIERARCHY",
                "checks": {"hierarchy": {"status": "ERROR", "reason": f"Extraction error: {e}", "suggestions": ""}}
            })
            
    return results
