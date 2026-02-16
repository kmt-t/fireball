#!/usr/bin/env python3
"""
Check C++ Coding Violations in Generated Headers

Detects prohibited patterns defined in cpp_coding_style.md:
- void* usage
- Dynamic memory allocation (malloc, new, etc.)
- Prohibited containers (std::vector, std::map, etc.)
- Exceptions (try, catch, throw)
- Manual resource management

Usage:
    python check_violations.py inc/gen/
"""

import sys
import os
from pathlib import Path

# Prohibited patterns and their error messages
VIOLATIONS = {
    r"void\s*\*": "void* is prohibited (use typed pointers or std::span)",
    r"\bmalloc\b": "malloc is prohibited (use RAII)",
    r"\bfree\b": "free is prohibited (use RAII destructors)",
    r"\bnew\b": "new is prohibited (use stack allocation or placement new)",
    r"\bdelete\b": "delete is prohibited (use RAII)",
    r"std::vector": "std::vector is prohibited in embedded code (use std::array or custom containers)",
    r"std::map": "std::map is prohibited (use flat containers)",
    r"std::string[^_]": "std::string is prohibited (use std::string_view or custom string)",
    r"\btry\b": "exceptions are prohibited (use result<T, E>)",
    r"\bcatch\b": "exceptions are prohibited (use result<T, E>)",
    r"\bthrow\b": "exceptions are prohibited (use result<T, E>)",
    r"using namespace std": "using namespace std is prohibited",
}

# Allowed patterns that might look like violations
ALLOWED_EXCEPTIONS = [
    "std::string_view",  # This is allowed
]


def check_file(file_path):
    """Check a single C++ file for violations. Returns True if no violations found."""
    violations_found = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
            # Check for allowed exceptions first
            for allowed in ALLOWED_EXCEPTIONS:
                content = content.replace(allowed, "")
            
            # Check each violation pattern
            import re
            for pattern, message in VIOLATIONS.items():
                if re.search(pattern, content):
                    violations_found.append(message)
    
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return False
    
    if violations_found:
        print(f"[ERROR] {file_path}:")
        for msg in violations_found:
            print(f"   - {msg}")
        return False
    
    return True


def check_directory(dir_path):
    """Check all C++ files in directory. Returns True if all files pass."""
    dir_path = Path(dir_path)
    
    if not dir_path.exists():
        print(f"Error: Directory not found: {dir_path}", file=sys.stderr)
        return False
    
    # Find all .hxx and .cxx files
    cpp_files = list(dir_path.glob("*.hxx")) + list(dir_path.glob("*.cxx"))
    
    if not cpp_files:
        print(f"Warning: No C++ files found in {dir_path}")
        return True
    
    all_passed = True
    for file_path in sorted(cpp_files):
        if not check_file(file_path):
            all_passed = False
    
    return all_passed


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <directory>", file=sys.stderr)
        sys.exit(1)
    
    target_dir = sys.argv[1]
    
    print(f"[*] Checking coding violations in {target_dir}...")
    
    if check_directory(target_dir):
        print("[OK] No violations found")
        sys.exit(0)
    else:
        print("[ERROR] Violations detected")
        sys.exit(1)


if __name__ == "__main__":
    main()
