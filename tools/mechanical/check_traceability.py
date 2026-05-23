import csv
from pathlib import Path
from tools.common.db import db
from tools.common.parser import parse_sections, extract_keywords

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPONENTS_DIR = REPO_ROOT / "docs" / "components"
SPEC_MATRIX_CSV = REPO_ROOT / "temp" / "spec_matrix.csv"
TRACE_MATRIX_CSV = REPO_ROOT / "temp" / "traceability_matrix.csv"

# Skip files
COMPONENT_SKIP = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}

def get_component_files() -> list[Path]:
    md_files = list(COMPONENTS_DIR.rglob('*.md'))
    return sorted([f for f in md_files if f.name not in COMPONENT_SKIP])

def check_traceability() -> tuple[list[dict], list[dict]]:
    """
    Runs M-TRACE-UNDEFINED, M-TRACE-ORPHAN-SEC, and M-TRACE-UNCOVERED.
    Returns: (violations list, warnings list)
    """
    violations = []
    warnings = []

    # 1. Load keywords from DB
    defined_keywords = db.load_defined_keywords()
    meta_keywords = db.load_meta_keywords()
    req_keywords_dict = db.load_requirement_keywords_dict()
    
    # Exclude templates
    TEMPLATE_KW_PREFIXES = {"Decision_", "Strategy_", "Requirement_", "req_", "concept", "Constraint_"}
    
    files = get_component_files()
    
    # Track which keywords are covered
    referenced_keywords = set()
    file_kw_map = {} # path_str -> set of keywords
    all_sections = []

    # Map to track sections by file
    for path in files:
        rel_path = str(path.relative_to(REPO_ROOT))
        file_sections = parse_sections(path)
        all_sections.extend(file_sections)
        
        file_kws = set()
        for sec in file_sections:
            file_kws.update(sec.keywords)
            referenced_keywords.update(sec.keywords)
            
            # Check M-TRACE-UNDEFINED
            # If a keyword is referenced but not in defined_keywords (and not a template)
            for kw in sec.keywords:
                if kw not in defined_keywords:
                    # Double check template prefix exclusion
                    if any(kw.startswith(prefix) for prefix in TEMPLATE_KW_PREFIXES):
                        continue
                    violations.append({
                        "rule_code": "M-TRACE-UNDEFINED",
                        "file_path": path,
                        "line_number": sec.line_start,
                        "message": f"未定義の要求キーワード: {{{kw}}}"
                    })

            # Check M-TRACE-ORPHAN-SEC
            if sec.has_content() and not sec.is_structural() and not sec.keywords:
                violations.append({
                    "rule_code": "M-TRACE-ORPHAN-SEC",
                    "file_path": path,
                    "line_number": sec.line_start,
                    "message": f"要求が紐付けられていないセクション: [{sec.heading}]"
                })
        
        file_kw_map[rel_path] = file_kws

    # Check M-TRACE-UNCOVERED
    # Find defined keywords that are never referenced
    for kw in defined_keywords:
        # Ignore meta keywords or templates
        if kw in meta_keywords or any(kw.startswith(prefix) for prefix in TEMPLATE_KW_PREFIXES):
            continue
        if kw not in referenced_keywords:
            warnings.append({
                "rule_code": "M-TRACE-UNCOVERED",
                "message": f"仕様書内で一度も紐付けられていない要求キーワード: {{{kw}}} ({req_keywords_dict.get(kw, 'No description')})"
            })

    # Update spec_matrix and traceability_matrix in DB
    all_kw_sorted = sorted(list(defined_keywords))
    all_files_sorted = sorted(list(file_kw_map.keys()))
    db.update_spec_matrix(all_kw_sorted, all_files_sorted, file_kw_map)
    
    # For traceability_matrix, load existing satisfied state to avoid wiping LLM check results
    existing_satisfied = db.load_traceability_matrix_satisfied()
    db.update_traceability_matrix(all_sections, existing_satisfied=existing_satisfied)

    # Generate CSV matrices
    save_spec_matrix_csv(all_kw_sorted, all_files_sorted, file_kw_map)
    save_traceability_matrix_csv(all_sections)

    return violations, warnings

def save_spec_matrix_csv(all_kw, all_files, file_kw_map):
    SPEC_MATRIX_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SPEC_MATRIX_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["component"] + all_kw)
        for fp in all_files:
            kws = file_kw_map.get(fp, set())
            writer.writerow([fp] + ["1" if k in kws else "0" for k in all_kw])
    print(f"✓ {SPEC_MATRIX_CSV.relative_to(REPO_ROOT)} を生成しました")

def save_traceability_matrix_csv(sections):
    TRACE_MATRIX_CSV.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_MATRIX_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            'file', 'section_depth', 'heading', 'keywords',
            'has_keyword', 'line_start', 'body_snippet'
        ])
        writer.writeheader()
        for sec in sections:
            rel_file = str(sec.file_path.relative_to(REPO_ROOT))
            writer.writerow({
                'file': rel_file,
                'section_depth': sec.level,
                'heading': sec.heading,
                'keywords': '|'.join(sec.keywords) if sec.keywords else '',
                'has_keyword': 'YES' if sec.keywords else 'NO',
                'line_start': sec.line_start,
                'body_snippet': sec.body[:100].replace('\n', ' '),
            })
    print(f"✓ {TRACE_MATRIX_CSV.relative_to(REPO_ROOT)} を生成しました")
