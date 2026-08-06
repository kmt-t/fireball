import unittest
import tempfile
from pathlib import Path
from tools.common.parser import (
    extract_keywords,
    Section,
    parse_sections,
    extract_sections_by_headers
)

class TestCommonParser(unittest.TestCase):
    def test_extract_keywords(self):
        text = "This section implements {Req_System_Init} and {Feature_A_01}. Ignore {Template_KW}."
        keywords = extract_keywords(text)
        self.assertIn("Req_System_Init", keywords)
        self.assertIn("Feature_A_01", keywords)
        # Template prefix keywords like Requirement_, req_, Decision_ etc should be filtered out if template pattern matches
        # KEYWORD_PATTERN matches {X}
        self.assertNotIn("Requirement_Sample", extract_keywords("Ref {Requirement_Sample}"))

    def test_section_properties(self):
        sec = Section(
            file_path=Path("dummy.md"),
            heading="1. コンセプト",
            level=2,
            body="Short body"
        )
        self.assertFalse(sec.has_content())
        self.assertTrue(sec.is_structural())

        sec_long = Section(
            file_path=Path("dummy.md"),
            heading="Custom Section",
            level=2,
            body="This is a long body content that exceeds fifty characters requirement easily."
        )
        self.assertTrue(sec_long.has_content())
        self.assertFalse(sec_long.is_structural())

    def test_parse_sections(self):
        content = """# Title
## 概要
This is summary section content.

## 詳細仕様
This section references {REQ_001} and {REQ_002}.
It has some details.
"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = Path(f.name)

        try:
            sections = parse_sections(temp_path)
            self.assertGreaterEqual(len(sections), 1)
            headings = [s.heading for s in sections]
            self.assertIn("概要", headings)
            self.assertIn("詳細仕様", headings)
        finally:
            temp_path.unlink()

    def test_extract_sections_by_headers(self):
        md_text = """## コンセプト
プロジェクトのコンセプトです。

## アルゴリズム
ソートアルゴリズムを使用します。
"""
        extracted = extract_sections_by_headers(md_text, ["コンセプト"])
        self.assertIn("プロジェクトのコンセプトです", extracted)
        self.assertNotIn("ソートアルゴリズム", extracted)

if __name__ == "__main__":
    unittest.main()
