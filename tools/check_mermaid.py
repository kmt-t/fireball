"""
tools/check_mermaid.py
Mermaid diagram syntax and structure validator for Fireball docs.
"""

import re
from pathlib import Path

docs_dir = Path(r"x:\hotspot\workspace\mysrc\fireball\docs")
mermaid_blocks = []

for md_file in sorted(docs_dir.glob("**/*.md")):
    content = md_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    in_mermaid = False
    start_line = 0
    buf = []
    for idx, line in enumerate(lines, start=1):
        if line.strip().startswith("```mermaid"):
            in_mermaid = True
            start_line = idx
            buf = []
        elif in_mermaid and line.strip().startswith("```"):
            in_mermaid = False
            mermaid_blocks.append((md_file, start_line, idx, buf))
        elif in_mermaid:
            buf.append(line)

print(f"Total mermaid diagrams found: {len(mermaid_blocks)}")

issues = []
for file_path, start, end, lines in mermaid_blocks:
    rel_path = file_path.relative_to(docs_dir)
    subgraph_count = 0
    end_count = 0

    non_empty = [l.strip() for l in lines if l.strip() and not l.strip().startswith("%%")]
    if not non_empty:
        issues.append((rel_path, start, "Empty mermaid block"))
        continue

    header = non_empty[0]

    for offset, line in enumerate(lines):
        line_no = start + 1 + offset
        sline = line.strip()
        if not sline or sline.startswith("%%"):
            continue

        if sline.startswith("subgraph"):
            subgraph_count += 1
        elif sline == "end" or sline.startswith("end ") or sline.startswith("end;"):
            end_count += 1

        # Check for unquoted parens in node labels (flowchart / graph / classDiagram)
        if not (header.startswith("sequenceDiagram") or header.startswith("stateDiagram") or header.startswith("classDiagram")):
            # Match unquoted brackets containing parentheses: e.g. A[Label (with parens)]
            m = re.search(r'(\b[A-Za-z0-9_]+)\[([^"\]]*\([^"\]]*\)[^"\]]*)\]', sline)
            if m:
                issues.append((rel_path, line_no, f"Unquoted parentheses in node label: {m.group(0)} -> should use quotes like {m.group(1)}[\"{m.group(2)}\"]"))

            # Match unquoted brackets containing colons or braces
            m_col = re.search(r'(\b[A-Za-z0-9_]+)\[([^"\]]*:[^"\]]*)\]', sline)
            if m_col:
                issues.append((rel_path, line_no, f"Unquoted colon in node label: {m_col.group(0)} -> should use quotes like {m_col.group(1)}[\"{m_col.group(2)}\"]"))

    if subgraph_count != end_count:
        issues.append((rel_path, start, f"Subgraph count ({subgraph_count}) does not match end count ({end_count})"))

print(f"\nDetected {len(issues)} issue(s) across diagrams:")
for path, line_no, msg in issues:
    print(f"  {path}:{line_no} - {msg}")
