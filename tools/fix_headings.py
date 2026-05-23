#!/usr/bin/env python3
import re
import sys
from pathlib import Path

# Setup path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.common.db import db

def fix_file(filepath: Path, heading_map: dict[str, str], complex_patterns: dict[str, str]) -> int:
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines()
    modified = False
    changes_count = 0

    for i, line in enumerate(lines):
        # First check complex patterns
        matched_complex = False
        for pattern, replacement in complex_patterns.items():
            try:
                if re.match(pattern, line.strip()):
                    lines[i] = replacement
                    matched_complex = True
                    modified = True
                    changes_count += 1
                    break
            except Exception as e:
                print(f"Warning: Invalid complex pattern regex '{pattern}': {e}")
        
        if matched_complex:
            continue

        # Standard check: #### `ident` ...
        m = re.match(r"^(####\s+)`([a-zA-Z_][a-zA-Z0-9_]*)`(\s*.*)$", line)
        if m:
            prefix, ident, rest = m.groups()
            
            # Check translation mapping
            if ident in heading_map:
                new_name = heading_map[ident]
                # If the mapping already includes the identifier in parentheses, just use it
                if new_name.startswith("####"):
                    lines[i] = new_name
                else:
                    lines[i] = f"#### {new_name}"
                
                print(f"  {filepath.name}:{i+1} : {line} -> {lines[i]}")
                modified = True
                changes_count += 1
            else:
                # Fallback: remove backquotes but preserve the text
                lines[i] = f"#### {ident}{rest}"
                print(f"  [Fallback] {filepath.name}:{i+1} : {line} -> {lines[i]}")
                modified = True
                changes_count += 1

    if modified:
        filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return changes_count

def main():
    components_dir = REPO_ROOT / "docs" / "components"
    tools_dir = REPO_ROOT / "docs" / "tools"
    
    # Load heading dictionary from DB
    heading_map = db.load_heading_dictionary()
    if not heading_map:
        print("Warning: Heading dictionary not found in DB. Attempting to sync directly from CSV...")
        import csv
        dict_file = tools_dir / "heading_dictionary.csv"
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
                heading_map = db.load_heading_dictionary()
            except Exception as e:
                print(f"Failed to sync heading dictionary fallback: {e}")

    # Load complex patterns from DB
    complex_patterns = db.load_complex_patterns()
    if not complex_patterns:
        print("Warning: Complex patterns not found in DB. Attempting to sync directly from CSV...")
        import csv
        patterns_file = tools_dir / "complex_patterns.csv"
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
                complex_patterns = db.load_complex_patterns()
            except Exception as e:
                print(f"Failed to sync complex patterns fallback: {e}")

    md_files = sorted(list(components_dir.glob("**/*.md")))
    total_changes = 0
    
    print("Fixing heading format (F1 violation) in specification files...")
    for fp in md_files:
        if fp.name in ["FORMAT.md", "CHECKLIST.md"]:
            continue
        changes = fix_file(fp, heading_map, complex_patterns)
        total_changes += changes

    print(f"Done. Fixed {total_changes} headings across components.")

if __name__ == "__main__":
    main()
