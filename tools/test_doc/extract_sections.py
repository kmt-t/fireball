#!/usr/bin/env python3
"""
Extract sections from Markdown documents for matrix-based analysis.

Output: List of sections with heading, keywords, and body content.
"""

import re
from pathlib import Path
from dataclasses import dataclass

KEYWORD_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")


@dataclass
class Section:
    """Represents a section in a Markdown document."""
    heading: str          # Section heading (e.g., "## IPC メカニズム")
    level: int            # Heading level (2 = ##, 3 = ###)
    keywords: list[str]   # Keywords found in this section
    body: str             # Full body text of this section


def extract_sections_from_file(filepath: Path) -> list[Section]:
    """
    Parse a Markdown file and extract sections (level 2+).
    Returns a list of Section objects.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        raise RuntimeError(f"Failed to read {filepath}: {e}")

    sections = []

    # Split by level 2+ headings
    heading_pattern = re.compile(r"^(#{2,}) (.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(content))

    for i, match in enumerate(matches):
        heading_text = match.group(2).strip()
        level = len(match.group(1))

        # Extract body: from this heading to the start of next section at same/higher level
        start = match.end()
        if i + 1 < len(matches):
            # Find next heading at same or higher level (fewer #)
            next_idx = i + 1
            while next_idx < len(matches) and len(matches[next_idx].group(1)) > level:
                next_idx += 1
            if next_idx < len(matches):
                end = matches[next_idx].start()
            else:
                end = len(content)
        else:
            end = len(content)

        body = content[start:end].strip()

        # Extract keywords
        keywords = list(set(KEYWORD_PATTERN.findall(body)))

        sections.append(Section(
            heading=heading_text,
            level=level,
            keywords=keywords,
            body=body
        ))

    return sections


def format_section_summary(section: Section) -> dict:
    """Convert a Section to a summary dict for JSON/CSV output."""
    return {
        "heading": section.heading,
        "level": section.level,
        "keywords": ", ".join(section.keywords),
        "body_length": len(section.body),
        "body_preview": section.body[:200] + "..." if len(section.body) > 200 else section.body
    }


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python extract_sections.py <markdown_file>")
        sys.exit(1)

    filepath = Path(sys.argv[1]).resolve()
    if not filepath.exists():
        print(f"File not found: {filepath}")
        sys.exit(1)

    sections = extract_sections_from_file(filepath)

    # Output as JSON
    output = [format_section_summary(s) for s in sections]
    print(json.dumps(output, ensure_ascii=False, indent=2))
