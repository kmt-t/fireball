import re
from pathlib import Path
from tools.common.parser import parse_md_tokens

# Constants
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = REPO_ROOT / "docs"
COMPONENTS_DIR = DOCS / "components"
REQUIREMENT_FILE = DOCS / "requires" / "requirement_list.md"

TIER_PATTERN = re.compile(r"\*\*Tier (\d+)")
_SNAKE_FUNC = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
_COMPONENT_SKIP = {"FORMAT.md", "CHECKLIST.md", "CONSISTENCY_MATRIX.md", "spec_matrix.csv", "traceability_matrix.csv"}

def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])

def _snake_to_kebab(name: str) -> str:
    return name.replace("_", "-")

def _load_api_aliases() -> tuple[dict[str, list[str]], set[str]]:
    aliases = {}
    skip = {"CONSISTENCY_MATRIX.md", REQUIREMENT_FILE.name}

    for path in COMPONENTS_DIR.rglob("*.md"):
        if path.name in _COMPONENT_SKIP:
            continue
        text = path.read_text(encoding="utf-8")
        tiers = TIER_PATTERN.findall(text)
        if not tiers or int(tiers[0]) != 1:
            continue

        skip.add(path.name)
        for token in parse_md_tokens(text):
            if token.get("type") != "heading" or token.get("attrs", {}).get("level") != 4:
                continue
            for child in token.get("children", []):
                if child.get("type") != "codespan":
                    continue
                name = child.get("raw", "")
                if not _SNAKE_FUNC.match(name):
                    continue
                camel = _snake_to_camel(name)
                kebab = _snake_to_kebab(name)
                variants = [v for v in (camel, kebab) if v != name]
                if variants:
                    existing = aliases.setdefault(name, [])
                    for v in variants:
                        if v not in existing:
                            existing.append(v)

    return aliases, skip

def check_api(all_files: list[Path]) -> list[dict]:
    ipc_aliases, api_skip = _load_api_aliases()
    violations = []
    for path in all_files:
        if path.name in api_skip:
            continue
        text = path.read_text(encoding="utf-8")
        for canonical, alias_list in ipc_aliases.items():
            for alias in alias_list:
                if alias in text:
                    # Find the line number of the alias in the file
                    lineno = 1
                    for idx, line in enumerate(text.splitlines(), 1):
                        if alias in line:
                            lineno = idx
                            break
                    violations.append({
                        "rule_code": "M-ARCH-NAMING",
                        "file_path": path,
                        "line_number": lineno,
                        "message": f"公開 API 命名違反: キャノニカル名 '{canonical}' に対して表記ゆれ '{alias}' が使用されています。"
                    })
    return violations
