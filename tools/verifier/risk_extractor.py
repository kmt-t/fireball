#!/usr/bin/env python3
"""
tools/verifier/risk_extractor.py
ドキュメントから形式検証リスクテーマ、不変式、モデル化制約を抽出するツール
"""
import os
import sys
import re
import json
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
for candidate in SCRIPT_PATH.parents:
    if (candidate / "tools").is_dir() and (candidate / "docs").is_dir():
        REPO_ROOT = candidate
        break
else:
    REPO_ROOT = Path.cwd()

def scan_risk_themes():
    docs_dir = REPO_ROOT / "docs"
    risk_themes = []
    
    # 1. Scan requirement_list.md for Challenge ADRs
    req_file = docs_dir / "requires" / "requirement_list.md"
    if req_file.exists():
        content = req_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            m = re.match(r"^\|\s*`\{([A-Za-z0-9_]+)\}`\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|", line)
            if m:
                kw, desc, status = m.group(1), m.group(2), m.group(3)
                if kw.startswith("Challenge_") or "TODO" in status:
                    risk_themes.append({
                        "id": f"RT-{kw}",
                        "source": "docs/requires/requirement_list.md",
                        "component": "System/Core",
                        "keyword": f"{{{kw}}}",
                        "title": kw.replace("Challenge_", ""),
                        "description": desc,
                        "status": status,
                        "category": "Design Challenge"
                    })

    # 2. Scan components/*.md for specific modeling points & invariants
    comp_dir = docs_dir / "components"
    if comp_dir.exists():
        for md_file in comp_dir.rglob("*.md"):
            rel_path = str(md_file.relative_to(REPO_ROOT)).replace("\\", "/")
            content = md_file.read_text(encoding="utf-8")
            
            # Find TLA+ / Verification modeling point sections
            sections = re.findall(r"####\s+検証対象となる制約事項\s*\((?:TLA\+[^)]*)?\)([\s\S]*?)(?=###|\Z)", content)
            for sec in sections:
                items = re.findall(r"-\s+\*\*([^*]+)\*\*:\s*(.+)", sec)
                for title, desc in items:
                    risk_themes.append({
                        "id": f"RT-{md_file.stem}-{re.sub(r'[^A-Za-z0-9]', '', title)}",
                        "source": rel_path,
                        "component": md_file.stem,
                        "keyword": f"{{{md_file.stem}}}",
                        "title": title.strip(),
                        "description": desc.strip(),
                        "status": "TODO",
                        "category": "Component Modeling Constraint"
                    })
                    
            # Find Invariant tables or definitions
            inv_matches = re.findall(r"-\s+不変式:\s*(.+)", content)
            for inv in inv_matches:
                risk_themes.append({
                    "id": f"RT-{md_file.stem}-Invariant",
                    "source": rel_path,
                    "component": md_file.stem,
                    "keyword": f"{{{md_file.stem}}}",
                    "title": "Invariant Definition",
                    "description": inv.strip(),
                    "status": "TODO",
                    "category": "Component Invariant"
                })

    return risk_themes

def main():
    themes = scan_risk_themes()
    output_path = REPO_ROOT / "tools" / "config" / "verification_risk_themes.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(themes, f, ensure_ascii=False, indent=2)

    print(f"Extracted {len(themes)} structured risk themes -> {output_path}")

if __name__ == "__main__":
    main()
