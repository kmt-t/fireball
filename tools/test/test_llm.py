import unittest
from pathlib import Path
from tools.llm.build_review_data import get_file_tier

class TestLLMTools(unittest.TestCase):
    def test_get_file_tier(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        req_path = repo_root / "docs" / "requires" / "requirement_list.md"
        core_path = repo_root / "docs" / "components" / "core" / "test.md"
        runtime_path = repo_root / "docs" / "components" / "runtime" / "test.md"
        platform_path = repo_root / "docs" / "components" / "platform" / "test.md"

        self.assertEqual(get_file_tier(req_path), 0)
        self.assertEqual(get_file_tier(core_path), 1)
        self.assertEqual(get_file_tier(runtime_path), 2)
        self.assertEqual(get_file_tier(platform_path), 3)

if __name__ == "__main__":
    unittest.main()
