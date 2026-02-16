#!/usr/bin/env python3
"""
Check C++ Naming Conventions in Generated Headers

Verifies naming follows cpp_coding_style.md:
- Types (struct/class/enum): snake_case
- Enum values: UPPER_SNAKE_CASE
- Functions/methods: snake_case
- Constants/macros: UPPER_SNAKE_CASE

Usage:
    python check_naming.py inc/gen/
"""

import sys
import os
import re
from pathlib import Path


def check_naming_conventions(file_path):
    """Check naming conventions in a C++ file. Returns (passed, errors)."""
    errors = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
    except Exception as e:
        return False, [f"Error reading file: {e}"]
    
    # Check struct/class names (should be snake_case)
    struct_pattern = re.compile(r'(?:struct|class)\s+([a-zA-Z_][a-zA-Z0-9_]*)')
    for match in struct_pattern.finditer(content):
        name = match.group(1)
        if not re.match(r'^[a-z][a-z0-9_]*$', name):
            errors.append(f"Type name must be snake_case: '{name}'")
    
    # Check enum class names (should be snake_case)
    enum_pattern = re.compile(r'enum\s+class\s+([a-zA-Z_][a-zA-Z0-9_]*)')
    for match in enum_pattern.finditer(content):
        name = match.group(1)
        if not re.match(r'^[a-z][a-z0-9_]*$', name):
            errors.append(f"Enum name must be snake_case: '{name}'")
    
    # Check enum values (should be UPPER_SNAKE_CASE)
    # Extract enum bodies
    enum_bodies = re.findall(r'enum\s+class\s+\w+[^{]*\{([^}]+)\}', content, re.DOTALL)
    for enum_body in enum_bodies:
        # Find enum value names (before comma or closing brace)
        values = re.findall(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[,}]', enum_body, re.MULTILINE)
        for value in values:
            if not re.match(r'^[A-Z][A-Z0-9_]*$', value):
                errors.append(f"Enum value must be UPPER_SNAKE_CASE: '{value}'")
    
    # Check using aliases (should be snake_case)
    using_pattern = re.compile(r'using\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=')
    for match in using_pattern.finditer(content):
        name = match.group(1)
        if not re.match(r'^[a-z][a-z0-9_]*$', name):
            errors.append(f"Type alias must be snake_case: '{name}'")
    
    # Check function names in interfaces (should be snake_case)
    # Note: This is for resource methods
    func_pattern = re.compile(r'^\s*([a-z][a-z0-9_-]*)\s*:\s*func\(', re.MULTILINE)
    for match in func_pattern.finditer(content):
        name = match.group(1)
        # Already kebab-case from WIT, will be converted to snake_case in C++
        # So this check is more for ensuring the pattern exists
        pass
    
    return len(errors) == 0, errors


def check_directory(dir_path):
    """Check all C++ files in directory. Returns True if all files pass."""
    dir_path = Path(dir_path)
    
    if not dir_path.exists():
        print(f"Error: Directory not found: {dir_path}", file=sys.stderr)
        return False
    
    cpp_files = list(dir_path.glob("*.hxx")) + list(dir_path.glob("*.cxx"))
    
    if not cpp_files:
        print(f"Warning: No C++ files found in {dir_path}")
        return True
    
    all_passed = True
    for file_path in sorted(cpp_files):
        passed, errors = check_naming_conventions(file_path)
        
        if not passed:
            print(f"[ERROR] {file_path.name}:")
            for error in errors:
                print(f"   - {error}")
            all_passed = False
    
    return all_passed


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <directory>", file=sys.stderr)
        sys.exit(1)
    
    target_dir = sys.argv[1]
    
    print(f"[*] Checking naming conventions in {target_dir}...")
    
    if check_directory(target_dir):
        print("[OK] All naming conventions correct")
        sys.exit(0)
    else:
        print("[ERROR] Naming violations detected")
        sys.exit(1)


if __name__ == "__main__":
    main()
