#!/usr/bin/env python3
import csv
import re
import sys
import json
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.common.db import db
from tools.common.llm import call_llm
from tools.common.parser import parse_sections, extract_keywords

COMPONENTS_DIR = REPO_ROOT / "docs" / "components"
REQUIREMENT_FILE = REPO_ROOT / "docs" / "requires" / "requirement_list.md"
CONFIG_DIR = REPO_ROOT / "tools" / "config"

def gather_component_files() -> list[Path]:
    md_files = list(COMPONENTS_DIR.rglob('*.md'))
    skip = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}
    return sorted([f for f in md_files if f.name not in skip])

def get_file_tier(file_path: Path) -> int:
    rel_path = str(file_path.relative_to(REPO_ROOT))
    if "docs/requires/" in rel_path:
        return 0
    elif "docs/components/core/" in rel_path or "docs/components/interface/" in rel_path:
        return 1
    elif "docs/components/runtime/" in rel_path or "docs/components/jit/" in rel_path:
        return 2
    elif "docs/components/platform/" in rel_path:
        return 3
    return 1

def judge_screening(sec_heading: str, sec_body: str, keywords: list[str],
                    backend: str = None, model: str = None) -> dict:
    """Uses LLM as a judge to dynamically determine which audit aspects apply to this section."""
    
    prompt = textwrap.dedent(f"""\
    あなたは設計書の品質管理責任者です。
    以下の設計書セクションのテキストを読み、このセクションについて【検証すべきアスペクト】を判定してください。
    
    【対象セクション】
    見出し: {sec_heading}
    本文:
    ---
    {sec_body[:2000]}
    ---
    紐付く要求キーワード: {", ".join(keywords)}
    
    【判定対象のアスペクト】
    1. policy_P01 (メモリ制約): このセクションに、メモリ確保、データ構造、バッファ、キュー、メモリの静的・動的割り当て、またはリソース管理に関する具体的な記述が含まれていますか？
    2. policy_P02 (例外禁止): このセクションに、エラーハンドリング、例外、try/catch、例外スロー、リカバリ戦略、または実行時型識別（RTTI/dynamic_cast等）に関する具体的な記述が含まれていますか？
    3. review_traceability (要求整合性): このセクションに、最上位要求（キーワード: {", ".join(keywords)}）が定義する役割や制約について、具体的な実現設計や詳細な仕様に関する記述が含まれていますか？
    4. review_quality (記述品質): このセクションは十分な長さと意味的な設計情報（不変条件、アルゴリズム、または設計判断など）を含んでおり、自己矛盾や曖昧さのチェックを行う価値がありますか？
    5. review_api (APIルール): このセクションに、具体的な関数定義、クラス定義、構造体、インターフェース仕様、APIのシグネチャ、または名前空間（namespace）に関する具体的な記述やコードが含まれていますか？
    
    【出力ルール】
    各アスペクトについて、検証の必要がある場合は "Yes"、ない場合は "No" と判定してください。
    以下のフォーマットのみで出力してください。余計な説明や前置きは一切出力しないでください。
    
    policy_P01: Yes または No
    policy_P02: Yes または No
    review_traceability: Yes または No
    review_quality: Yes または No
    review_api: Yes または No
    """)
    
    try:
        raw = call_llm(prompt, backend=backend, model=model, max_tokens=256, apply_contract=False)
    except Exception as e:
        print(f"Warning: LLM call failed in judge_screening: {e}")
        raw = ""
        
    res = {
        "policy_P01": "N/A",
        "policy_P02": "N/A",
        "review_traceability": "N/A",
        "review_quality": "N/A",
        "review_api": "N/A"
    }
    
    for line in raw.splitlines():
        m = re.match(r"^(policy_P01|policy_P02|review_traceability|review_quality|review_api)\s*:\s*(yes|no)", line.strip(), re.IGNORECASE)
        if m:
            aspect = m.group(1)
            val = m.group(2).lower()
            res[aspect] = "PENDING" if val == "yes" else "N/A"
            
    return res

def build_and_sync_all():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    comp_files = gather_component_files()
    
    # 1. Document Tiers
    tiers_data = []
    req_rel = str(REQUIREMENT_FILE.relative_to(REPO_ROOT))
    tiers_data.append({
        "file_path": req_rel,
        "tier": 0,
        "parent_file": ""
    })
    
    for f in comp_files:
        rel = str(f.relative_to(REPO_ROOT))
        tier = get_file_tier(f)
        tiers_data.append({
            "file_path": rel,
            "tier": tier,
            "parent_file": req_rel
        })
        
    # Write to CSV
    tiers_csv = CONFIG_DIR / "document_tiers.csv"
    with open(tiers_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["file_path", "tier", "parent_file"])
        writer.writeheader()
        writer.writerows(tiers_data)
    print(f"Generated {tiers_csv}")
    
    # Sync to DB
    db.sync_document_tiers(tiers_data)
    print("Synchronized document_tiers to DB.")

    # 2. Parse all sections
    all_sections = []
    if REQUIREMENT_FILE.exists():
        all_sections.extend(parse_sections(REQUIREMENT_FILE))
    for f in comp_files:
        all_sections.extend(parse_sections(f))

    # 3. Keyword Sections Map
    kw_sections_data = []
    defined_keywords = db.load_defined_keywords()
    
    for sec in all_sections:
        rel_file = str(sec.file_path.relative_to(REPO_ROOT))
        for kw in sec.keywords:
            if kw in defined_keywords:
                kw_sections_data.append({
                    "keyword": kw,
                    "file_path": rel_file,
                    "heading": sec.heading,
                    "line_start": sec.line_start
                })
                
    # Write to CSV
    kw_sec_csv = CONFIG_DIR / "keyword_sections.csv"
    with open(kw_sec_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["keyword", "file_path", "heading", "line_start"])
        writer.writeheader()
        writer.writerows(kw_sec_csv_rows := sorted(kw_sections_data, key=lambda x: (x["keyword"], x["file_path"], x["heading"])))
    print(f"Generated {kw_sec_csv}")
    
    # Sync to DB
    db.sync_keyword_sections(kw_sec_csv_rows)
    print("Synchronized keyword_sections to DB.")

    # 4. Review Matrix Generation with Screening (LLM as a Judge)
    matrix_data = []
    total_secs = len(all_sections)
    print(f"Analyzing {total_secs} sections for review matrix (LLM as a Judge)...")
    
    # DB APIを読んで現在の review_matrix 状態をロード
    existing_matrix = {}
    try:
        existing_matrix = {
            (row["file_path"], row["heading"]): row 
            for row in db.load_review_matrix()
        }
    except Exception as e:
        print(f"Warning: Failed to load existing review matrix from DB: {e}")

    for idx, sec in enumerate(all_sections, 1):
        rel_file = str(sec.file_path.relative_to(REPO_ROOT))
        is_tier0 = (sec.file_path == REQUIREMENT_FILE)
        is_exempt = sec.is_structural()
        
        # 構造的セクションなどは N/A 固定
        if is_tier0 or is_exempt or len(sec.body.strip()) < 50:
            matrix_data.append({
                "file_path": rel_file,
                "heading": sec.heading,
                "keywords": ",".join(sec.keywords),
                "policy_P01": "N/A",
                "policy_P02": "N/A",
                "review_traceability": "N/A",
                "review_quality": "N/A",
                "review_api": "N/A",
                "llm_checked": 0
            })
            continue

        # 既存のマトリクス定義があれば、過去の割り当て状態を引き継ぐ
        # ※ただし、キーワードに変更があった場合は、割り当てるアスペクトが変わる可能性があるため、再スクリーニング（LLMコール）を実行する
        existing_row = existing_matrix.get((rel_file, sec.heading))
        if existing_row is not None:
            existing_kws = set(filter(None, existing_row.get("keywords", "").split(",")))
            current_kws = set(sec.keywords)
            
            if existing_kws == current_kws:
                matrix_data.append({
                    "file_path": rel_file,
                    "heading": sec.heading,
                    "keywords": ",".join(sec.keywords),
                    "policy_P01": existing_row["policy_P01"],
                    "policy_P02": existing_row["policy_P02"],
                    "review_traceability": existing_row["review_traceability"],
                    "review_quality": existing_row["review_quality"],
                    "review_api": existing_row["review_api"],
                    "llm_checked": existing_row["llm_checked"]
                })
                continue

        # 新規追加されたセクションのみ、LLM Screeningを実行
        print(f"  [{idx}/{total_secs}] Judging screening for new section: {rel_file} #{sec.heading}...")
        
        # Check Cache
        content_hash = db.make_hash_key(sec.body, ",".join(sec.keywords))
        hash_key = db.make_hash_key("SCREENING-JUDGE", rel_file, sec.heading, content_hash)
        
        cached = db.get_cache(hash_key)
        if cached is not None:
            try:
                judge_res = json.loads(cached["suggestions"])
            except Exception:
                judge_res = None
        else:
            judge_res = None
            
        if judge_res is None:
            # LLM as a judge call
            judge_res = judge_screening(sec.heading, sec.body, sec.keywords)
            
            # Cache the judge result
            db.set_cache(
                hash_key=hash_key,
                rule_code="SCREENING",
                target_type="screening_judge",
                file_path=rel_file,
                heading=sec.heading,
                status="PASS",
                reason="Screening judge results cached.",
                suggestions=json.dumps(judge_res),
                input_hash=content_hash
            )
            
        matrix_data.append({
            "file_path": rel_file,
            "heading": sec.heading,
            "keywords": ",".join(sec.keywords),
            "policy_P01": judge_res.get("policy_P01", "N/A"),
            "policy_P02": judge_res.get("policy_P02", "N/A"),
            "review_traceability": judge_res.get("review_traceability", "N/A"),
            "review_quality": judge_res.get("review_quality", "N/A"),
            "review_api": judge_res.get("review_api", "N/A"),
            "llm_checked": 0
        })
        
    # Write to CSV
    matrix_csv = CONFIG_DIR / "review_matrix.csv"
    with open(matrix_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "file_path", "heading", "keywords", 
            "policy_P01", "policy_P02", 
            "review_traceability", "review_quality", "review_api", 
            "llm_checked"
        ])
        writer.writeheader()
        writer.writerows(matrix_data)
    print(f"Generated {matrix_csv}")
    
    # Sync to DB
    db.sync_review_matrix(matrix_data)
    print("Synchronized review_matrix to DB.")

if __name__ == "__main__":
    build_and_sync_all()
