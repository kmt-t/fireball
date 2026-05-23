import re
from pathlib import Path

_CPP_IDENT_HEADING = re.compile(r"^####\s+`[a-zA-Z_][a-zA-Z0-9_]*`")
_CPP_FENCE = re.compile(r"^```(cpp|c\+\+|cxx|c)$", re.IGNORECASE)
_NON_MERMAID_DIAGRAM_FENCE = re.compile(
    r"^(plantuml|uml|graphviz|dot|ditaa|blockdiag|nwdiag|seqdiag|actdiag)$",
    re.IGNORECASE,
)
_MERMAID_KEYWORD = re.compile(
    r"^(graph |sequenceDiagram|stateDiagram|classDiagram|flowchart |gantt|"
    r"gitGraph|pie |erDiagram|journey|timeline|mindmap|block-beta|architecture-beta)"
)

def check_format(component_files: list[Path]) -> list[dict]:
    violations = []

    # 1. M-FORMAT-HEADING
    for path in component_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CPP_IDENT_HEADING.match(line):
                violations.append({
                    "rule_code": "M-FORMAT-HEADING",
                    "file_path": path,
                    "line_number": lineno,
                    "message": f"見出しがC++識別子で始まっています: {line.strip()}"
                })

    # 2. M-FORMAT-CODE
    for path in component_files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CPP_FENCE.match(line.strip()):
                violations.append({
                    "rule_code": "M-FORMAT-CODE",
                    "file_path": path,
                    "line_number": lineno,
                    "message": f"C/C++ コードブロックが使用されています: {line.strip()}"
                })

    # 3. M-FORMAT-MERMAID
    for path in component_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        in_fence = False
        fence_lang = ""
        fence_body = []
        fence_start = 0
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("```") and not in_fence:
                in_fence = True
                fence_lang = stripped[3:].strip()
                fence_body = []
                fence_start = lineno
            elif stripped.startswith("```") and in_fence:
                in_fence = False
                if _NON_MERMAID_DIAGRAM_FENCE.match(fence_lang):
                    violations.append({
                        "rule_code": "M-FORMAT-MERMAID",
                        "file_path": path,
                        "line_number": fence_start,
                        "message": f"非Mermaidダイアグラムツール: ```{fence_lang}"
                    })
                elif fence_lang != "mermaid":
                    body = "\n".join(fence_body)
                    if _MERMAID_KEYWORD.search(body):
                        violations.append({
                            "rule_code": "M-FORMAT-MERMAID",
                            "file_path": path,
                            "line_number": fence_start,
                            "message": f"Mermaid内容に ```mermaid タグなし (```{fence_lang or '(なし)'})"
                        })
                fence_body = []
                fence_lang = ""
            elif in_fence:
                fence_body.append(line)

    return violations
