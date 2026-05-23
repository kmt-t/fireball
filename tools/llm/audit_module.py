import os
from pathlib import Path
from tools.common.db import db
from tools.common.llm import call_llm, parse_llm_json_response
from tools.common.parser import parse_sections, extract_keywords

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

def load_project_policies() -> dict[str, str]:
    rules_dir = REPO_ROOT / ".claude" / "rules"
    policies = {}
    policy_files = ["development-policy.md", "stdlib_policy.md", "embedded_cpp.md"]

    for pf in policy_files:
        path = rules_dir / pf
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text.startswith("---") or (text.startswith("name:") and "---" in text):
                parts = text.split("---", 1)
                text = parts[1] if len(parts) > 1 else text
            policies[pf] = text.strip()
        else:
            policies[pf] = ""
    return policies

def audit_policy(file_path: Path, backend: str = None, model: str = None, max_tokens: int = 1024) -> dict:
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"status": "ERROR", "reason": f"Failed to read file: {e}"}

    policies = load_project_policies()
    dev_policy = policies.get("development-policy.md", "")
    stdlib_policy = policies.get("stdlib_policy.md", "")
    embedded_policy = policies.get("embedded_cpp.md", "")

    model_name = model or "default"
    backend_name = backend or "auto"
    input_hash = db.make_hash_key(content, str(policies), model_name, backend_name)
    rel_path = str(file_path.relative_to(REPO_ROOT))
    hash_key = db.make_hash_key("S-POLICY-MEM", rel_path, input_hash)

    cached = db.get_cache(hash_key)
    if cached is not None:
        return cached

    prompt = f"""\
あなたはいかなる場合でもヒープメモリ確保を行わず例外処理も使用しないC++23組み込みシステム(Fireball Hypervisor)の仕様書査読者です。
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
ファイル名: {file_path.name}
---
{content[:6000]}
---

【検証項目 (S-POLICY-MEM)】
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
    raw_response = call_llm(prompt, backend=backend, model=model, max_tokens=max_tokens)
    res = parse_llm_json_response(raw_response)

    if res.get("status") in ["PASS", "FAIL", "UNCERTAIN"]:
        db.set_cache(
            hash_key,
            rule_code="S-POLICY-MEM",
            target_type="file",
            file_path=rel_path,
            heading="",
            status=res["status"],
            reason=res.get("reason", ""),
            suggestions=res.get("suggestions", ""),
            input_hash=input_hash
        )
    return res

def audit_quality(file_path: Path, backend: str = None, model: str = None, max_tokens: int = 1024) -> dict:
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"status": "ERROR", "reason": f"Failed to read file: {e}"}

    model_name = model or "default"
    backend_name = backend or "auto"
    input_hash = db.make_hash_key(content, model_name, backend_name)
    rel_path = str(file_path.relative_to(REPO_ROOT))

    cache_placeholder = db.get_cache(db.make_hash_key("S-QUALITY-PLACEHOLDER", rel_path, input_hash))
    cache_ambiguity = db.get_cache(db.make_hash_key("S-QUALITY-AMBIGUITY", rel_path, input_hash))
    cache_api = db.get_cache(db.make_hash_key("S-QUALITY-API", rel_path, input_hash))

    if cache_placeholder and cache_ambiguity and cache_api:
        return {
            "S-QUALITY-PLACEHOLDER": cache_placeholder,
            "S-QUALITY-AMBIGUITY": cache_ambiguity,
            "S-QUALITY-API": cache_api
        }

    prompt = f"""\
あなたは組み込みハイパーバイザ(Fireball)のドキュメント品質査読者です。
対象ドキュメントについて、以下の品質チェック項目を厳しく判定してください。

【対象ドキュメント】
ファイル名: {file_path.name}
---
{content[:6000]}
---

【検証項目】
1. S-QUALITY-PLACEHOLDER (プレースホルダーの検出):
   `TBD`、`TODO`、`未定`、`後述`、`要検討` などの仮置きテキストや書きかけの箇所が残っていないか。
2. S-QUALITY-AMBIGUITY (曖昧な記述の排除):
   「適切な処理」「必要に応じて」「状況に応じて」「〜など」「〜等」のように、具体的な振る舞いやインターフェイスが曖昧に濁されている部分がないか。
3. S-QUALITY-API (API定義の完全性):
   公開API、URI/IPCインターフェイス等の関数定義において、引数・戻り値の型・説明、エラーコードの説明が不足・欠落していないか。

【出力フォーマット】
以下のJSONフォーマットのみで回答してください。JSON以外のテキストは一切出力しないでください。
{{
  "S-QUALITY-PLACEHOLDER": {{
    "status": "PASS" または "FAIL",
    "reason": "合格・不合格の根拠",
    "suggestions": "具体的な修正箇所と修正案"
  }},
  "S-QUALITY-AMBIGUITY": {{
    "status": "PASS" または "FAIL",
    "reason": "合格・不合格の根拠",
    "suggestions": "具体的な修正箇所と修正案"
  }},
  "S-QUALITY-API": {{
    "status": "PASS" または "FAIL",
    "reason": "合格・不合格の根拠",
    "suggestions": "具体的な修正箇所と修正案"
  }}
}}
"""
    raw_response = call_llm(prompt, backend=backend, model=model, max_tokens=max_tokens)
    res = parse_llm_json_response(raw_response)

    final_res = {}
    for code in ["S-QUALITY-PLACEHOLDER", "S-QUALITY-AMBIGUITY", "S-QUALITY-API"]:
        if code in res:
            final_res[code] = res[code]
        else:
            final_res[code] = {
                "status": res.get("status", "FAIL") if "status" in res else "FAIL",
                "reason": res.get("reason", "Failed to parse individual rules"),
                "suggestions": res.get("suggestions", "")
            }

        item = final_res[code]
        if item.get("status") in ["PASS", "FAIL", "UNCERTAIN"]:
            h_key = db.make_hash_key(code, rel_path, input_hash)
            db.set_cache(
                h_key,
                rule_code=code,
                target_type="file",
                file_path=rel_path,
                heading="",
                status=item["status"],
                reason=item.get("reason", ""),
                suggestions=item.get("suggestions", ""),
                input_hash=input_hash
            )
    return final_res

def audit_trace_alignment(file_path: Path, backend: str = None, model: str = None, max_tokens: int = 1024) -> dict:
    try:
        sections = parse_sections(file_path)
    except Exception as e:
        return {"status": "ERROR", "reason": f"Failed to parse sections: {e}"}

    defined_keywords = db.load_defined_keywords()
    meta_keywords = db.load_meta_keywords()
    req_keywords_dict = db.load_requirement_keywords_dict()
    
    TEMPLATE_KW_PREFIXES = {"Decision_", "Strategy_", "Requirement_", "req_", "concept", "Constraint_"}
    rel_path = str(file_path.relative_to(REPO_ROOT))

    l1_issues_map = {}
    results = []

    model_name = model or "default"
    backend_name = backend or "auto"

    for sec in sections:
        sec_req_kws = []
        for kw in sec.keywords:
            if kw in defined_keywords and kw not in meta_keywords:
                if not any(kw.startswith(prefix) for prefix in TEMPLATE_KW_PREFIXES):
                    sec_req_kws.append(kw)

        if not sec_req_kws:
            continue

        req_descriptions = []
        for kw in sec_req_kws:
            desc = req_keywords_dict.get(kw, "No description")
            req_descriptions.append(f"- `{{{kw}}}`: {desc}")

        req_context = "\n".join(req_descriptions)

        input_hash = db.make_hash_key(sec.heading, sec.body[:1000], req_context, model_name, backend_name)
        hash_key = db.make_hash_key("S-TRACE-ALIGN", rel_path, sec.heading, input_hash)

        cached = db.get_cache(hash_key)
        if cached is not None:
            status = cached["status"]
            reason = cached["reason"]
            suggestions = cached["suggestions"]
        else:
            prompt = f"""\
あなたは組み込みハイパーバイザ(Fireball)の仕様整合性チェッカーです。
対象セクションの記述が、紐付けられた要求キーワード `{{Keyword}}` の定義と意味的に整合するか（要件を満たしているか、矛盾がないか）を検証してください。

【要求定義リスト】
{req_context}

【対象セクション】
見出し: {sec.heading}
本文:
{sec.body[:1500]}

【出力フォーマット】
以下のJSONフォーマットのみで回答してください。JSON以外のテキストは一切出力しないでください。
{{
  "status": "PASS" または "FAIL",
  "reason": "PASSの場合は『要求事項が正しく充足されている』旨、FAILの場合はどのキーワードが不足・矛盾しているかその具体的な理由（日本語、1〜2行程度）",
  "suggestions": "不整合を解消するための具体的なドキュメント修正案（Markdown形式）"
}}
"""
            try:
                raw_response = call_llm(prompt, backend=backend, model=model, max_tokens=max_tokens)
                res = parse_llm_json_response(raw_response)
                status = res.get("status", "FAIL")
                reason = res.get("reason", "Parse failed")
                suggestions = res.get("suggestions", "")
            except Exception as e:
                status = "ERROR"
                reason = f"LLM execution error: {e}"
                suggestions = ""

            if status in ["PASS", "FAIL", "UNCERTAIN"]:
                db.set_cache(
                    hash_key,
                    rule_code="S-TRACE-ALIGN",
                    target_type="section",
                    file_path=rel_path,
                    heading=sec.heading,
                    status=status,
                    reason=reason,
                    suggestions=suggestions,
                    input_hash=input_hash
                )

        l1_issues_map[(rel_path, sec.heading)] = status
        results.append({
            "section": sec.heading,
            "keywords": sec_req_kws,
            "status": status,
            "reason": reason,
            "suggestions": suggestions
        })

    if sections:
        existing_satisfied = db.load_traceability_matrix_satisfied()
        db.update_traceability_matrix(sections, l1_issues_map=l1_issues_map, existing_satisfied=existing_satisfied)

    overall_status = "PASS"
    fail_reasons = []
    all_suggestions = []
    for r in results:
        if r["status"] == "FAIL":
            overall_status = "FAIL"
            fail_reasons.append(f"セクション [{r['section']}]: {r['reason']}")
            if r["suggestions"]:
                all_suggestions.append(f"### Section: {r['section']}\n{r['suggestions']}")
        elif r["status"] == "ERROR":
            if overall_status != "FAIL":
                overall_status = "ERROR"
            fail_reasons.append(f"セクション [{r['section']}]: {r['reason']}")

    return {
        "status": overall_status,
        "reason": "; ".join(fail_reasons) if fail_reasons else "すべてのセクションの要求キーワードが正しく充足されています。",
        "suggestions": "\n\n".join(all_suggestions) if all_suggestions else "",
        "section_results": results
    }
