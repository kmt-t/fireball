#!/usr/bin/env python3
import os
import sys
import re
import csv
import argparse
from pathlib import Path

# Setup path
SCRIPT_PATH = Path(__file__).resolve()
for candidate in SCRIPT_PATH.parents:
    if (candidate / "tools").is_dir() and (candidate / "verify").is_dir() and (candidate / "docs").is_dir():
        REPO_ROOT = candidate
        break
else:
    raise RuntimeError("Could not locate repository root from run_audit.py")

sys.path.insert(0, str(REPO_ROOT))

from tools.common.db import db
from tools.mechanical.check_format import check_format
from tools.mechanical.check_traceability import check_traceability
from tools.mechanical.check_api import check_api
from tools.mechanical.check_mermaid import check_mermaid
from tools.llm.audit_consistency import run_matrix_audit
from tools.llm.build_review_data import build_and_sync_all

# Terminal Colors
RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"

def sync_keywords_and_glossary():
    keywords_data = []
    
    # 1. Parse requirement_list.md for normal keywords & glossary
    req_file = REPO_ROOT / "docs" / "requires" / "requirement_list.md"
    if req_file.exists():
        content = req_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        
        category = "General"
        for line in lines:
            h_match = re.match(r"^###?\s+(.+)$", line)
            if h_match:
                category = h_match.group(1).strip()
            
            kw_match = re.match(r"^\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|(.*)$", line.strip())
            if kw_match:
                kw = kw_match.group(1).strip()
                rest = kw_match.group(2).strip()
                parts = [p.strip() for p in rest.split('|')]
                if parts and not parts[-1]:
                    parts.pop()
                desc = parts[0] if len(parts) > 0 else ""
                priority = parts[1] if len(parts) > 1 else ""
                method = parts[2] if len(parts) > 2 else ""
                
                keywords_data.append({
                    "keyword": kw,
                    "description": desc,
                    "priority": priority,
                    "verification_method": method,
                    "category": category,
                    "is_meta": 0,
                    "is_global": 0
                })
        
        # Parse glossary definitions
        glossary_data = []
        glossary_pattern = re.compile(r"^-\s+\*\*([A-Za-z0-9_ -]+)\*\*:\s*(.+)$")
        in_glossary = False
        for line in lines:
            if "## 6. 用語定義" in line or "## 用意定義" in line or "## 用語定義" in line:
                # To handle variants
                if "## 6. 用語定義" in line or "## 用語定義" in line:
                    in_glossary = True
                    continue
            if in_glossary and line.startswith("##"):
                in_glossary = False
            if in_glossary:
                m = glossary_pattern.match(line.strip())
                if m:
                    term, definition = m.groups()
                    glossary_data.append({
                        "term": term.strip(),
                        "definition": definition.strip()
                    })
        
        db.sync_glossary(glossary_data)

    # 2. Parse document_structure.md for meta-keywords and global-keywords
    doc_struct = REPO_ROOT / "docs" / "architecture" / "document_structure.md"
    if doc_struct.exists():
        content = doc_struct.read_text(encoding="utf-8")
        
        in_meta = False
        in_global = False
        pattern = re.compile(r"\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|\s*([^|]+?)\s*\|")
        
        for line in content.splitlines():
            if "## 4. メタキーワード" in line:
                in_meta = True
                in_global = False
                continue
            if "## 5. グローバルキーワード" in line:
                in_meta = False
                in_global = True
                continue
            if line.startswith("##") and not ("メタキーワード" in line or "グローバルキーワード" in line):
                in_meta = False
                in_global = False
                
            m = pattern.match(line.strip())
            if m:
                kw, desc = m.groups()
                if kw in ["キーワード", "META_キーワード", "GLOBAL_キーワード"]:
                    continue
                keywords_data.append({
                    "keyword": kw.strip(),
                    "description": desc.strip(),
                    "priority": "N/A",
                    "verification_method": "N/A",
                    "category": "Meta" if in_meta else "Global",
                    "is_meta": 1 if in_meta else 0,
                    "is_global": 1 if in_global else 0
                })

    db.sync_keywords(keywords_data)

    # 3. Parse heading_dictionary.csv and sync it
    dict_file = REPO_ROOT / "tools" / "config" / "heading_dictionary.csv"
    if dict_file.exists():
        dict_data = []
        try:
            with dict_file.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("identifier") and row.get("translation"):
                        dict_data.append({
                            "identifier": row["identifier"].strip(),
                            "translation": row["translation"].strip()
                        })
            db.sync_heading_dictionary(dict_data)
        except Exception as e:
            print(f"Warning: Failed to sync heading dictionary: {e}")

    # 4. Parse complex_patterns.csv and sync it
    patterns_file = REPO_ROOT / "tools" / "config" / "complex_patterns.csv"
    if patterns_file.exists():
        patterns_data = []
        try:
            with patterns_file.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("pattern") and row.get("replacement"):
                        patterns_data.append({
                            "pattern": row["pattern"].strip(),
                            "replacement": row["replacement"].strip()
                        })
            db.sync_complex_patterns(patterns_data)
        except Exception as e:
            print(f"Warning: Failed to sync complex patterns: {e}")



def gather_component_files() -> list[Path]:
    components_dir = REPO_ROOT / "docs" / "components"
    md_files = list(components_dir.rglob('*.md'))
    skip = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md"}
    return sorted([f for f in md_files if f.name not in skip])

def main():
    parser = argparse.ArgumentParser(description="Fireball Unified Documentation Audit Tool")
    
    # Modes
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--sync", action="store_true", help="Only synchronize keywords and glossary to SQLite database and exit")
    mode_group.add_argument("--gentable", action="store_true", help="Generate specs matrix and consistency checklist")
    mode_group.add_argument("--llm", action="store_true", help="Run LLM consistency check from checklist CSV")
    
    # Configurations
    parser.add_argument("--backend", choices=["sakura", "openrouter", "gemini", "ollama"], help="Force LLM backend")
    parser.add_argument("--model", type=str, help="Override LLM model name")
    parser.add_argument("--max-tokens", type=int, default=2048, help="Max tokens in LLM generation")

    args = parser.parse_args()

    # 1. Sync keywords and glossary
    sync_keywords_and_glossary()
    if args.sync:
        print("✓ Database synchronization completed successfully.")
        sys.exit(0)

    # 2. Gentable Mode (Matrix and Keyword List Generation)
    if args.gentable:
        print(f"\n{BOLD}■ Generating Matrix & Checklist (--gentable){RESET}")
        build_and_sync_all()
        sys.exit(0)

    # 3. Checklist LLM Mode (Matrix LLM Audit)
    if args.llm:
        print(f"\n{BOLD}■ Running Consistency Checklist Audit (--llm){RESET}")
        failures = run_matrix_audit(backend=args.backend, model=args.model, max_tokens=args.max_tokens)
        if failures > 0:
            print(f"\n{RED}✖ Consistency checklist check failed with {failures} issues.{RESET}")
            sys.exit(1)
        print(f"\n{GREEN}✔ All consistency checklist items passed!{RESET}")
        sys.exit(0)

    # Default Mode: Mechanical checks only
    print(f"\n{BOLD}■ Running Mechanical Checks (Format, Traceability, Mermaid, API){RESET}")
    total_violations = 0
    total_warnings = 0
    files_to_test = gather_component_files()

    # Format Check
    print("Running Format Checks...", end="", flush=True)
    violations_format = check_format(files_to_test)
    if not violations_format:
        print(f" {GREEN}✓ PASS{RESET}")
    else:
        print(f" {RED}✗ FAIL{RESET} ({len(violations_format)} violations)")
        for v in violations_format:
            total_violations += 1
            rel_p = v["file_path"].relative_to(REPO_ROOT)
            print(f"  - {v['rule_code']} ({rel_p}:{v['line_number']}) - {v['message']}")

    # Traceability Check
    print("Running Traceability Checks...", end="", flush=True)
    violations_trace, warnings_trace = check_traceability()
    if not violations_trace and not warnings_trace:
        print(f" {GREEN}✓ PASS{RESET}")
    else:
        if violations_trace:
            print(f" {RED}✗ FAIL{RESET} ({len(violations_trace)} violations)")
            for v in violations_trace:
                total_violations += 1
                rel_p = v["file_path"].relative_to(REPO_ROOT) if "file_path" in v else ""
                loc = f" ({rel_p}:{v['line_number']})" if rel_p else ""
                print(f"  - {v['rule_code']}{loc} - {v['message']}")
        else:
            print(f" {GREEN}✓ PASS{RESET} (with warnings)")
        for w in warnings_trace:
            total_warnings += 1
            print(f"  - {YELLOW}⚠ WARN{RESET} {w['rule_code']} - {w['message']}")

    # Mermaid Check
    print("Running Mermaid Syntax Checks...", end="", flush=True)
    violations_mermaid = check_mermaid(files_to_test)
    if not violations_mermaid:
        print(f" {GREEN}✓ PASS{RESET}")
    else:
        print(f" {RED}✗ FAIL{RESET} ({len(violations_mermaid)} violations)")
        for v in violations_mermaid:
            total_violations += 1
            rel_p = v["file_path"].relative_to(REPO_ROOT)
            print(f"  - {v['rule_code']} ({rel_p}:{v['line_number']}) - {v['message']}")

    # API Check
    print("Running API Naming Checks...", end="", flush=True)
    violations_api = check_api(files_to_test)
    if not violations_api:
        print(f" {GREEN}✓ PASS{RESET}")
    else:
        print(f" {RED}✗ FAIL{RESET} ({len(violations_api)} violations)")
        for v in violations_api:
            total_violations += 1
            rel_p = v["file_path"].relative_to(REPO_ROOT)
            print(f"  - M-ARCH-NAMING ({rel_p}:{v['line_number']}) - {v['message']}")

    print(f"\n{'='*60}")
    print(f"Audit Summary: {total_violations} Failures, {total_warnings} Warnings")
    if total_violations > 0:
        sys.exit(1)
    else:
        print(f"{GREEN}✔ All mechanical checks passed!{RESET}")
        sys.exit(0)

if __name__ == "__main__":
    main()
