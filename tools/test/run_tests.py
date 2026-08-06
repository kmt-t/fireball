#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

def main():
    test_dir = Path(__file__).resolve().parent
    repo_root = test_dir.parent.parent
    sys.path.insert(0, str(repo_root))

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(test_dir), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if not result.wasSuccessful():
        sys.exit(1)

if __name__ == "__main__":
    main()
