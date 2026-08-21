"""
tools/validate_mermaid.py
Comprehensive Mermaid syntax and structure validator for Markdown documentation.
"""

import re
from pathlib import Path

class MermaidValidator:
    def __init__(self):
        self.errors = []

    def validate_diagram(self, rel_path: str, start_line: int, lines: list[str]) -> list[str]:
        diagram_errors = []
        non_empty = [l for l in lines if l.strip() and not l.strip().startswith("%%")]
        if not non_empty:
            return [f"Line {start_line}: Empty mermaid diagram block"]

        header_line = non_empty[0].strip()
        first_word = header_line.split()[0] if header_line.split() else ""

        # Diagram Type classification
        if first_word in ("graph", "flowchart"):
            diagram_errors.extend(self._validate_flowchart(start_line, lines))
        elif first_word == "sequenceDiagram":
            diagram_errors.extend(self._validate_sequence(start_line, lines))
        elif first_word in ("stateDiagram", "stateDiagram-v2"):
            diagram_errors.extend(self._validate_state(start_line, lines))
        elif first_word == "classDiagram":
            diagram_errors.extend(self._validate_class(start_line, lines))
        elif first_word in ("erDiagram", "gantt", "pie", "mindmap", "timeline", "gitGraph", "quadrantChart", "xychart-beta"):
            # Basic validation
            pass
        else:
            diagram_errors.append(f"Line {start_line}: Unknown or invalid diagram type '{first_word}'")

        return diagram_errors

    def _validate_flowchart(self, start_line: int, lines: list[str]) -> list[str]:
        errors = []
        subgraph_stack = []

        for idx, line in enumerate(lines):
            line_no = start_line + 1 + idx
            s = line.strip()
            if not s or s.startswith("%%"):
                continue

            if s.startswith("subgraph"):
                subgraph_stack.append(line_no)
            elif s == "end" or s.startswith("end ") or s.startswith("end;"):
                if not subgraph_stack:
                    errors.append(f"Line {line_no}: Unexpected 'end' without matching 'subgraph'")
                else:
                    subgraph_stack.pop()

            # Check unquoted bracket node labels containing parentheses, braces, colons, or nested brackets
            # e.g., Node[Label (with parens)] -> invalid in Mermaid unless Node["Label (with parens)"]
            for m in re.finditer(r'(\b[A-Za-z0-9_]+)\[([^"\]\n]*[(){}:,][^"\]\n]*)\]', s):
                node_id = m.group(1)
                inner = m.group(2)
                # Ensure it is not preceded by a quote
                if not inner.startswith('"'):
                    errors.append(f"Line {line_no}: Unquoted special character in node label '{node_id}[{inner}]'. Wrap label in double quotes: '{node_id}[\"{inner}\"]'")

            # Check for broken bracket nesting like A[Text [nested] more]
            if re.search(r'\[[^"\]\n]*\[[^"\]\n]*\]', s):
                errors.append(f"Line {line_no}: Malformed/nested brackets in node definition: '{s}'")

        if subgraph_stack:
            errors.append(f"Line {subgraph_stack[-1]}: Unclosed 'subgraph' (missing 'end')")

        return errors

    def _validate_sequence(self, start_line: int, lines: list[str]) -> list[str]:
        errors = []
        block_stack = []

        for idx, line in enumerate(lines):
            line_no = start_line + 1 + idx
            s = line.strip()
            if not s or s.startswith("%%") or s == "sequenceDiagram":
                continue

            # Sequence blocks that require 'end': alt, opt, loop, par, critical, rect, group
            first = s.split()[0] if s.split() else ""
            if first in ("alt", "opt", "loop", "par", "critical", "rect", "group"):
                block_stack.append((first, line_no))
            elif first == "else":
                if not block_stack or block_stack[-1][0] not in ("alt", "critical", "par"):
                    errors.append(f"Line {line_no}: 'else' without matching 'alt' or 'critical' block")
            elif first == "end" or s == "end" or s.startswith("end;"):
                if not block_stack:
                    errors.append(f"Line {line_no}: Unexpected 'end' without matching sequence block (opt/alt/loop/rect/par)")
                else:
                    block_stack.pop()

        for blk, blk_line in block_stack:
            errors.append(f"Line {blk_line}: Unclosed sequence block '{blk}' (missing 'end')")

        return errors

    def _validate_state(self, start_line: int, lines: list[str]) -> list[str]:
        errors = []
        composite_stack = []

        for idx, line in enumerate(lines):
            line_no = start_line + 1 + idx
            s = line.strip()
            if not s or s.startswith("%%") or s in ("stateDiagram", "stateDiagram-v2"):
                continue

            if "{" in s and not s.endswith("}"):
                composite_stack.append(line_no)
            elif s == "}" or s.startswith("}"):
                if not composite_stack:
                    errors.append(f"Line {line_no}: Unexpected '}}' without matching state block")
                else:
                    composite_stack.pop()

        for blk_line in composite_stack:
            errors.append(f"Line {blk_line}: Unclosed state composite block '{{' (missing '}}')")

        return errors

    def _validate_class(self, start_line: int, lines: list[str]) -> list[str]:
        errors = []
        class_stack = []

        for idx, line in enumerate(lines):
            line_no = start_line + 1 + idx
            s = line.strip()
            if not s or s.startswith("%%") or s == "classDiagram":
                continue

            if "{" in s and not s.endswith("}"):
                class_stack.append(line_no)
            elif s == "}" or s.startswith("}"):
                if not class_stack:
                    errors.append(f"Line {line_no}: Unexpected '}}' without matching class block")
                else:
                    class_stack.pop()

        for blk_line in class_stack:
            errors.append(f"Line {blk_line}: Unclosed class block '{{' (missing '}}')")

        return errors


def run_validation(docs_root: Path):
    validator = MermaidValidator()
    total_diagrams = 0
    all_issues = []

    for md_file in sorted(docs_root.glob("**/*.md")):
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
                total_diagrams += 1
                rel_path = str(md_file.relative_to(docs_root)).replace("\\", "/")
                errs = validator.validate_diagram(rel_path, start_line, buf)
                for e in errs:
                    all_issues.append((rel_path, e))
            elif in_mermaid:
                buf.append(line)

    print(f"Scanned {total_diagrams} Mermaid diagrams across documentation.")
    if not all_issues:
        print("[OK] ALL MERMAID DIAGRAMS VALID.")
    else:
        print(f"[FAIL] Found {len(all_issues)} Mermaid syntax issue(s):")
        for rel_path, err in all_issues:
            print(f"  [{rel_path}] {err}")

    return len(all_issues)


if __name__ == "__main__":
    import sys
    docs_path = Path(r"x:\hotspot\workspace\mysrc\fireball\docs")
    issues_count = run_validation(docs_path)
    sys.exit(1 if issues_count > 0 else 0)
