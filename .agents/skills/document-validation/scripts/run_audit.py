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
from tools.llm.audit_module import audit_policy, audit_quality, audit_trace_alignment
from tools.llm.audit_consistency import audit_pair_files, run_checklist_audit
from tools.mechanical.check_consistency import generate_checklist, read_csv_checklist, save_csv_checklist
from tools.llm.audit_hierarchy import audit_hierarchy_tier

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
            # Section detection
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

def print_result_header(title: str):
    print(f"\n{BOLD}{CYAN}[{title}]{RESET}")

def main():
    parser = argparse.ArgumentParser(description="Fireball Unified Documentation Audit Tool")
    
    # Modes
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--module", type=str, help="Audit a specific markdown file")
    mode_group.add_argument("--all", action="store_true", help="Audit all component specifications")
    mode_group.add_argument("--pair", type=str, nargs=2, metavar=("FILE_A", "FILE_B"), help="Audit boundary consistency between two files")
    mode_group.add_argument("--hierarchy", action="store_true", help="Audit abstraction levels and trace validation across tiers")
    mode_group.add_argument("--sync", action="store_true", help="Only synchronize keywords and glossary to SQLite database and exit")
    mode_group.add_argument("--gentable", action="store_true", help="Generate specs matrix and consistency checklist")
    mode_group.add_argument("--llm", action="store_true", help="Run LLM checks from checklist CSV")
    # Filters and Configurations
    parser.add_argument("--rule", action="append", help="Run only specific rules (e.g., --rule M-FORMAT-HEADING --rule S-POLICY-MEM)")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], help="Tier to audit when using --hierarchy")
    parser.add_argument("--backend", choices=["sakura", "openrouter", "gemini", "ollama"], help="Force LLM backend")
    parser.add_argument("--model", type=str, help="Override LLM model name")
    parser.add_argument("--max-tokens", type=int, default=2048, help="Max tokens in LLM generation")
    parser.add_argument("--quick", action="store_true", help="Quick mode (Tier 1 only)")

    # Unused arguments kept for backward compatibility with check_consistency/audit_traceability
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    # 1. Sync keywords and glossary
    sync_keywords_and_glossary()
    if args.sync:
        print("✓ Database synchronization completed successfully.")
        sys.exit(0)

    # Validate hierarchy tier
    if args.hierarchy and args.tier is None:
        parser.error("--hierarchy requires --tier <1|2|3> to be specified.")

    total_violations = 0
    total_warnings = 0

    # 2. Gentable Mode
    if args.gentable:
        print(f"\n{BOLD}■ Generating Matrix & Checklist (--gentable){RESET}")
        # Run mechanical traceability to generate matrices
        violations, warnings = check_traceability()
        # Load spec matrix to generate checklist
        comp_files = gather_component_files()
        file_kw_map = {}
        defined_kw = db.load_defined_keywords()
        for f in comp_files:
            text = f.read_text(encoding="utf-8")
            kws = {m for m in re.findall(r"\{([A-Za-z0-9_]+)\}", text) if m in defined_kw}
            file_kw_map[str(f.relative_to(REPO_ROOT))] = kws

        checklist_items = generate_checklist(
            [str(f.relative_to(REPO_ROOT)) for f in comp_files], 
            file_kw_map, 
            backend=args.backend, 
            model=args.model, 
            max_tokens=args.max_tokens
        )
        if checklist_items:
            save_csv_checklist(checklist_items)
            print(f"  ✓ Consistency checklist generated: {len(checklist_items)} items")
        sys.exit(0)

    # 3. Checklist LLM Mode
    if args.llm:
        print(f"\n{BOLD}■ Running Consistency Checklist Audit (--llm){RESET}")
        items = read_csv_checklist()
        if not items:
            print(f"  {YELLOW}Warning: Checklist CSV is empty or not found.{RESET}")
            sys.exit(1)
        failures = run_checklist_audit(items, backend=args.backend, model=args.model)
        if failures > 0:
            print(f"\n{RED}✖ Consistency checklist check failed with {failures} issues.{RESET}")
            sys.exit(1)
        print(f"\n{GREEN}✔ All consistency checklist items passed!{RESET}")
        sys.exit(0)

    # 4. Pair Mode
    if args.pair:
        file_a = Path(args.pair[0]).resolve()
        file_b = Path(args.pair[1]).resolve()
        if not file_a.exists() or not file_b.exists():
            print(f"ERROR: Specified files do not exist.")
            sys.exit(1)

        print(f"Auditing pairwise consistency: {file_a.name} x {file_b.name}...")
        res = audit_pair_files(file_a, file_b, backend=args.backend, model=args.model, max_tokens=args.max_tokens)
        status = res.get("status", "FAIL")
        reason = res.get("reason", "N/A")
        suggestions = res.get("suggestions", "")
        
        if status == "PASS":
            print(f"  {GREEN}✓ PASS{RESET}  S-ARCH-PAIR")
            sys.exit(0)
        elif status in ("WARN", "UNCERTAIN"):
            print(f"  {YELLOW}⚠ WARN{RESET}  S-ARCH-PAIR")
            print(f"        理由: {reason}")
            if suggestions:
                print(f"        改善案:\n{suggestions}")
            sys.exit(0)
        else:
            print(f"  {RED}✗ FAIL{RESET}  S-ARCH-PAIR")
            print(f"        理由: {reason}")
            if suggestions:
                print(f"        改善案:\n{suggestions}")
            sys.exit(1)

    # 5. Hierarchy Mode
    if args.hierarchy:
        print(f"Running Hierarchy Audit - Tier {args.tier}...")
        results = audit_hierarchy_tier(args.tier, backend=args.backend, model=args.model, max_tokens=args.max_tokens)
        failures = 0
        for r in results:
            chk = r["checks"]["hierarchy"]
            status = chk.get("status", "FAIL")
            if status != "PASS":
                failures += 1
                print(f"  {RED}✗ FAIL{RESET} {r['file']}")
                print(f"        理由: {chk.get('reason', '')}")
                if chk.get("suggestions"):
                    print(f"        改善案:\n{chk.get('suggestions')}")
            else:
                print(f"  {GREEN}✓ PASS{RESET} {r['file']}")

        if failures > 0:
            print(f"\n{RED}✖ Hierarchy check failed with {failures} issues.{RESET}")
            sys.exit(1)
        print(f"\n{GREEN}✔ Tier {args.tier} hierarchy checks passed!{RESET}")
        sys.exit(0)

    # 6. Module/All mode or Default Mechanical check
    files_to_test = []
    if args.module:
        p = Path(args.module).resolve()
        if not p.exists():
            print(f"ERROR: Specified file {args.module} does not exist.")
            sys.exit(1)
        files_to_test.append(p)
    else:
        # Default/All component spec files
        files_to_test = gather_component_files()

    if not files_to_test:
        print(f"{YELLOW}Warning: No specification files found.{RESET}")
        sys.exit(0)

    # Determine which rules to run
    target_rules = args.rule or []

    # Perform audits
    # A. Mechanical Checks
    # formatting checks
    if not target_rules or any(r.startswith("M-FORMAT") for r in target_rules):
        violations_format = check_format(files_to_test)
        if target_rules:
            violations_format = [v for v in violations_format if v["rule_code"] in target_rules]
        
        print_result_header("M-FORMAT: Formatting Constraints")
        if not violations_format:
            print(f"  {GREEN}✓ PASS{RESET}")
        else:
            for v in violations_format:
                total_violations += 1
                rel_p = v["file_path"].relative_to(REPO_ROOT)
                print(f"  {RED}✗ FAIL{RESET} {v['rule_code']} ({rel_p}:{v['line_number']}) - {v['message']}")

    # traceability mechanical checks
    if not target_rules or any(r.startswith("M-TRACE") for r in target_rules):
        violations_trace, warnings_trace = check_traceability()
        if target_rules:
            violations_trace = [v for v in violations_trace if v["rule_code"] in target_rules]
            warnings_trace = [w for w in warnings_trace if w["rule_code"] in target_rules]
        
        print_result_header("M-TRACE: Mechanical Traceability")
        if not violations_trace and not warnings_trace:
            print(f"  {GREEN}✓ PASS{RESET}")
        else:
            for v in violations_trace:
                total_violations += 1
                rel_p = v["file_path"].relative_to(REPO_ROOT) if "file_path" in v else ""
                loc = f" ({rel_p}:{v['line_number']})" if rel_p else ""
                print(f"  {RED}✗ FAIL{RESET} {v['rule_code']}{loc} - {v['message']}")
            for w in warnings_trace:
                total_warnings += 1
                print(f"  {YELLOW}⚠ WARN{RESET} {w['rule_code']} - {w['message']}")

    # Mermaid diagram syntax check
    if not target_rules or any(r.startswith("M-MERMAID") for r in target_rules):
        violations_mermaid = check_mermaid(files_to_test)
        if target_rules:
            violations_mermaid = [v for v in violations_mermaid if v["rule_code"] in target_rules]

        print_result_header("M-MERMAID: Mermaid Diagram Syntax")
        if not violations_mermaid:
            print(f"  {GREEN}✓ PASS{RESET}")
        else:
            for v in violations_mermaid:
                total_violations += 1
                rel_p = v["file_path"].relative_to(REPO_ROOT)
                print(f"  {RED}✗ FAIL{RESET} {v['rule_code']} ({rel_p}:{v['line_number']}) - {v['message']}")

    # api naming check
    if not target_rules or "M-ARCH-NAMING" in target_rules:
        violations_api = check_api(files_to_test)
        print_result_header("M-ARCH: Mechanical Architecture Naming")
        if not violations_api:
            print(f"  {GREEN}✓ PASS{RESET}")
        else:
            for v in violations_api:
                total_violations += 1
                rel_p = v["file_path"].relative_to(REPO_ROOT)
                print(f"  {RED}✗ FAIL{RESET} M-ARCH-NAMING ({rel_p}:{v['line_number']}) - {v['message']}")

    # B. Semantic Checks (LLM-based)
    # Only run S-* checks if either --all or --module is specified or a specific S-* rule is requested.
    run_semantic = args.all or args.module or any(r.startswith("S-") for r in target_rules)
    
    if run_semantic:
        print_result_header("S-*: Semantic Checks (LLM-based)")
        for path in files_to_test:
            rel_p = path.relative_to(REPO_ROOT)
            print(f"\nAuditing {BOLD}{rel_p}{RESET}:")
            
            # Policy
            if not target_rules or "S-POLICY-MEM" in target_rules:
                print(f"  ├─ S-POLICY-MEM: Memory/STL Policy...", end="", flush=True)
                res = audit_policy(path, backend=args.backend, model=args.model, max_tokens=args.max_tokens)
                status = res.get("status", "FAIL")
                if status == "PASS":
                    print(f" {GREEN}✓ PASS{RESET}")
                else:
                    total_violations += 1
                    print(f" {RED}✗ FAIL{RESET}")
                    print(f"        理由: {res.get('reason', '')}")
                    if res.get("suggestions"):
                        print(f"        改善案:\n{res.get('suggestions')}")
            
            # Quality
            quality_rules = ["S-QUALITY-PLACEHOLDER", "S-QUALITY-AMBIGUITY", "S-QUALITY-API"]
            if not target_rules or any(qr in target_rules for qr in quality_rules):
                print(f"  ├─ S-QUALITY-*: Checking Placeholders, Ambiguity, API completeness...", end="", flush=True)
                res_q = audit_quality(path, backend=args.backend, model=args.model, max_tokens=args.max_tokens)
                print(" Done")
                for qrule in quality_rules:
                    if target_rules and qrule not in target_rules:
                        continue
                    item = res_q.get(qrule, {"status": "FAIL", "reason": "Not checked"})
                    status = item.get("status", "FAIL")
                    if status == "PASS":
                        print(f"    - {GREEN}✓ PASS{RESET} {qrule}")
                    else:
                        total_violations += 1
                        print(f"    - {RED}✗ FAIL{RESET} {qrule}")
                        print(f"        理由: {item.get('reason', '')}")
                        if item.get("suggestions"):
                            print(f"        改善案:\n{item.get('suggestions')}")

            # Trace Align
            if not target_rules or "S-TRACE-ALIGN" in target_rules:
                print(f"  └─ S-TRACE-ALIGN: Trace Alignment...", end="", flush=True)
                res = audit_trace_alignment(path, backend=args.backend, model=args.model, max_tokens=args.max_tokens)
                status = res.get("status", "FAIL")
                if status == "PASS":
                    print(f" {GREEN}✓ PASS{RESET}")
                else:
                    total_violations += 1
                    print(f" {RED}✗ FAIL{RESET}")
                    print(f"        理由: {res.get('reason', '')}")
                    if res.get("suggestions"):
                        print(f"        改善案:\n{res.get('suggestions')}")

    print(f"\n{'='*60}")
    print(f"Audit Summary: {total_violations} Failures, {total_warnings} Warnings")
    if total_violations > 0:
        sys.exit(1)
    else:
        print(f"{GREEN}✔ All checks passed!{RESET}")
        sys.exit(0)

if __name__ == "__main__":
    main()
