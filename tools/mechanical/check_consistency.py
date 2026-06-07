import csv
import re
from pathlib import Path
from tools.common.db import db
from tools.common.parser import parse_md_tokens, heading_text, token_text

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

def generate_checklist(all_files: list[str], file_kw_map: dict[str, set[str]], backend: str = None, model: str = None, max_tokens: int = 8192) -> list[dict]:
    """Generates consistency checklist mechanically with pruning (filters global keywords & merges items per file pair)."""
    kw_section_map = _build_keyword_section_map(all_files, file_kw_map)

    # 1. Filter out global policy keywords loaded from database
    global_keywords = db.load_global_keywords()
    filtered_kw_section_map = {}
    for kw, mentions in kw_section_map.items():
        if kw not in global_keywords:
            filtered_kw_section_map[kw] = mentions

    # 2. Group shared keywords and their sections at the file-pair level
    file_pairs = {} # (file_a, file_b) -> { "keywords": set, "sections_a": set, "sections_b": set }

    for kw, mentions in sorted(filtered_kw_section_map.items()):
        for i in range(len(mentions)):
            for j in range(i + 1, len(mentions)):
                file_a = mentions[i]["file"]
                file_b = mentions[j]["file"]

                # Ensure deterministic ordering of file pairs
                if file_a > file_b:
                    file_a, file_b = file_b, file_a
                    sec_a_list, sec_b_list = mentions[j]["sections"], mentions[i]["sections"]
                else:
                    sec_a_list, sec_b_list = mentions[i]["sections"], mentions[j]["sections"]

                pair_key = (file_a, file_b)
                if pair_key not in file_pairs:
                    file_pairs[pair_key] = {
                        "keywords": set(),
                        "sections_a": set(),
                        "sections_b": set()
                    }
                file_pairs[pair_key]["keywords"].add(kw)
                file_pairs[pair_key]["sections_a"].update(sec_a_list)
                file_pairs[pair_key]["sections_b"].update(sec_b_list)

    # 3. Create exactly 1 checklist item per file pair
    items = []
    pair_idx = 1

    for (file_a, file_b), data in sorted(file_pairs.items()):
        pair_id = f"G{pair_idx:02d}"
        pair_idx += 1

        kw_list = sorted(list(data["keywords"]))
        kw_list_str = ", ".join(f"{{{k}}}" for k in kw_list)
        
        # Use "|" separator for section headings to handle headings with commas safely
        sec_a_str = "|".join(sorted(list(data["sections_a"])))
        sec_b_str = "|".join(sorted(list(data["sections_b"])))

        aspect = "A" # Default to API/Interface consistency aspect
        check_content = f"共有要求 {kw_list_str} に関して、仕様記述に不整合がないか確認してください。"

        items.append({
            "pair_id": pair_id,
            "file_a": file_a,
            "file_b": file_b,
            "shared_keywords": ",".join(kw_list),
            "file_a_section": sec_a_str,
            "file_b_section": sec_b_str,
            "check_num": "1",
            "aspect": aspect,
            "check_content": check_content,
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
