import unittest
import tempfile
from pathlib import Path
from tools.mechanical.check_format import check_format
from tools.mechanical.check_mermaid import check_mermaid

class TestMechanical(unittest.TestCase):
    def test_check_format_valid(self):
        content = """# Valid Document
## Section 1
This is clean markdown text.
```mermaid
graph TD
    A --> B
```
"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = Path(f.name)

        try:
            violations = check_format([temp_path])
            self.assertEqual(len(violations), 0)
        finally:
            temp_path.unlink()

    def test_check_format_violations(self):
        content = """# Header
#### `c_identifier_heading`
```cpp
int main() { return 0; }
```
"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = Path(f.name)

        try:
            violations = check_format([temp_path])
            rule_codes = [v["rule_code"] for v in violations]
            self.assertIn("M-FORMAT-HEADING", rule_codes)
            self.assertIn("M-FORMAT-CODE", rule_codes)
        finally:
            temp_path.unlink()

    def test_check_mermaid_valid(self):
        content = """```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Running: start
```
"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = Path(f.name)

        try:
            violations = check_mermaid([temp_path])
            # Valid mermaid diagram should have no syntax violations
            self.assertEqual(len(violations), 0)
        finally:
            temp_path.unlink()

if __name__ == "__main__":
    unittest.main()
