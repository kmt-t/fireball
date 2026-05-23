import re
import mistune
from pathlib import Path
from dataclasses import dataclass, field

KEYWORD_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")
TEMPLATE_KW_PATTERN = {"Decision_", "Strategy_", "Requirement_", "req_", "concept", "Constraint_"}

STRUCTURAL_HEADINGS = {
    "概要", "コンセプト", "1. コンセプト",
    "静的モデル", "2. 静的モデル",
    "データ構造", "2.1 データ構造",
    "内部ブロック図", "2.2 内部ブロック図",
    "主要なクラス・構造体・配列・定数", "2.3 主要なクラス・構造体・配列・定数",
    "動的モデル", "3. 動的モデル",
    "アルゴリズム", "3.1 アルゴリズム",
    "状態遷移図", "3.2 状態遷移図",
    "状態遷移", "4.2 状態遷移",
    "内部シーケンス", "3.3 内部シーケンス",
    "インターフェイス定義", "4. インターフェイス定義",
    "インターフェイス設計", "5. インターフェイス設計",
    "公開API", "4.1 公開API", "5.1 公開API",
    "URI/IPCインターフェイス", "4.2 URI/IPCインターフェイス", "5.2 URI/IPCインターフェイス",
    "制約達成の方策", "5. 制約達成の方策", "6. 制約達成の方策",
    "性能制約と方策", "5.1 性能制約と方策", "6.1 性能制約と方策",
    "用語", "用語定義", "用語集",
    "参考", "参考実装", "参考実装リスト", "参考資料",
    "変更履歴", "履歴",
    "命名規則", "命名規約",
    "設計判断", "設計判断の記録", "ADR",
    "フィードバック", "制限事項", "トレードオフ",
    "検証", "6. 検証"
}

@dataclass
class Section:
    file_path: Path
    heading: str
    level: int            # Heading level (2=##, 3=###, etc.)
    body: str
    keywords: list[str] = field(default_factory=list)
    line_start: int = 0

    def has_content(self) -> bool:
        """Checks if body has meaningful content length (>= 50 chars)."""
        return len(self.body.strip()) >= 50

    def is_structural(self) -> bool:
        """Determines if the section is standard structural/skipping heading."""
        for exempt in STRUCTURAL_HEADINGS:
            if exempt.lower() in self.heading.lower():
                return True
        # Code identifier in backticks or parentheses
        if re.search(r'`[a-zA-Z0-9_\-]+`|[\(（][a-zA-Z0-9_\-\s/]+[\)）]', self.heading):
            return True
        # Numeric table headings (e.g. 2.1 Control Flow)
        if re.match(r'^[0-9]+\.[0-9]+\s+', self.heading):
            return True
        # Lists headings
        if re.match(r'^[0-9]+\.\s*(コマンド|応答|命令|リスト)', self.heading):
            return True
        return False

def extract_keywords(text: str) -> list[str]:
    """Extracts requirement keywords from text, ignoring templates."""
    matches = KEYWORD_PATTERN.findall(text)
    return [m for m in matches if not any(m.startswith(prefix) for prefix in TEMPLATE_KW_PATTERN)]

def parse_sections(file_path: Path) -> list[Section]:
    """Parses a markdown specification file and returns list of Section entries."""
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    sections = []
    section_stack = []  # List of (level, Section)

    for i, line in enumerate(lines, 1):
        # Match headings ##, ###, ####, #####, ######
        match = re.match(r'^(#{2,6})\s+(.+)$', line)
        if not match:
            continue

        level = len(match.group(1))
        heading = match.group(2).strip()

        # Pop lower/same level sections from stack to finish them
        if section_stack and level <= section_stack[-1][0]:
            while section_stack and level <= section_stack[-1][0]:
                prev_level, prev_section = section_stack.pop()
                sections.append(prev_section)

        # Create new section
        sec = Section(
            file_path=file_path,
            heading=heading,
            level=level,
            body="",
            line_start=i
        )
        section_stack.append((level, sec))

    # Empty stack
    for level, sec in section_stack:
        sections.append(sec)

    # Collect body text and keywords
    for idx, sec in enumerate(sections):
        start = sec.line_start
        # Find next section start line
        if idx + 1 < len(sections):
            end = sections[idx + 1].line_start
        else:
            end = len(lines) + 1

        body_lines = lines[start:end - 1]
        sec.body = '\n'.join(body_lines)
        
        # Extract keywords
        h_kws = extract_keywords(sec.heading)
        b_kws = extract_keywords(sec.body)
        # Unique list keeping order
        sec.keywords = list(dict.fromkeys(h_kws + b_kws))

    return sec_list_filter(sections)

def sec_list_filter(sections: list[Section]) -> list[Section]:
    # Filters out any section that was created but has invalid start or negative bounds
    return [s for s in sections if s.line_start > 0]

_md_parser = mistune.create_markdown(renderer=None)

def parse_md_tokens(text: str) -> list[dict]:
    return _md_parser(text) or []

def heading_text(token: dict) -> str:
    return "".join(child.get("raw", "") for child in token.get("children", []))

def token_text(token: dict) -> str:
    tp = token.get("type", "")
    if tp == "blank_line":
        return ""
    if tp == "block_code":
        info = token.get("attrs", {}).get("info", "")
        return f"```{info}\n{token.get('raw', '')}\n```"
    children = token.get("children", [])
    if children:
        return "".join(token_text(c) for c in children)
    return token.get("raw", "")

def extract_sections_by_headers(text: str, headers: list[str], max_chars: int = 2000) -> str:
    tokens = parse_md_tokens(text)
    result = []
    capturing = False
    current = []

    for token in tokens:
        if token.get("type") == "heading":
            level = token.get("attrs", {}).get("level", 0)
            if level <= 3:
                if capturing and current:
                    result.append("\n".join(filter(None, current)))
                h_text = heading_text(token)
                capturing = any(h.lower() in h_text.lower() for h in headers)
                current = [f"{'#' * level} {h_text}"] if capturing else []
                continue
        if capturing:
            chunk = token_text(token)
            if chunk:
                current.append(chunk)

    if capturing and current:
        result.append("\n".join(filter(None, current)))

    combined = "\n\n".join(result)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n...(省略)..."
    return combined or "(対象セクションが見つかりませんでした)"
