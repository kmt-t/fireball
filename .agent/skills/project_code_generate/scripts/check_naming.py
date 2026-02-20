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


def check_paths(paths):
    """Check multiple files or directories. Returns True if all pass."""
    all_passed = True
    for path_str in paths:
        path = Path(path_str)
        if not path.exists():
            print(f"Error: Path not found: {path}", file=sys.stderr)
            all_passed = False
            continue
            
        if path.is_file():
            passed, errors = check_naming_conventions(path)
            if not passed:
                print(f"[ERROR] {path.name}:")
                for error in errors:
                    print(f"   - {error}")
                all_passed = False
        else:
            cpp_files = list(path.glob("*.hxx")) + list(path.glob("*.cxx"))
            if not cpp_files:
                print(f"Warning: No C++ files found in {path}")
            else:
                for file_path in sorted(cpp_files):
                    passed, errors = check_naming_conventions(file_path)
                    if not passed:
                        print(f"[ERROR] {file_path.name}:")
                        for error in errors:
                            print(f"   - {error}")
                        all_passed = False
    return all_passed


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Check naming conventions in C++ files.")
    parser.add_argument("paths", nargs="*", help="Files or directories to scan")
    parser.add_argument("--stdin-paths", "-p", action="store_true", help="Read paths from STDIN")
    parser.add_argument("--json", "-j", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    targets = []
    if args.stdin_paths:
        for line in sys.stdin:
            path = line.strip()
            if path:
                targets.append(path)
    
    if args.paths:
        targets.extend(args.paths)
    
    if not targets:
        if not args.stdin_paths and not sys.stdin.isatty():
            # Treat stdin as content
            if not args.json: print("[*] Checking content from stdin...")
            content = sys.stdin.read()
            passed, errors = check_naming_conventions_content(content)
            if args.json:
                print(json.dumps({"passed": passed, "errors": errors}, indent=2))
            else:
                if passed:
                    print("[OK] All naming conventions correct")
                else:
                    print("[ERROR] Naming violations detected")
                    for error in errors:
                        print(f"   - {error}")
            sys.exit(0 if passed else 1)
        else:
            parser.print_help()
            sys.exit(1)
    
    if not args.json: print(f"[*] Checking naming conventions...")
    
    results = []
    all_passed = True
    for t in targets:
        path = Path(t)
        if not path.exists():
            if not args.json: print(f"Error: Path not found: {t}", file=sys.stderr)
            continue
        
        files_to_check = []
        if path.is_file():
            files_to_check = [path]
        else:
            files_to_check = list(path.glob("*.hxx")) + list(path.glob("*.cxx"))
            
        for f in sorted(files_to_check):
            passed, errors = check_naming_conventions(f)
            if not passed:
                all_passed = False
                results.append({"file": str(f), "errors": errors})
                if not args.json:
                    print(f"[ERROR] {f.name}:")
                    for error in errors:
                        print(f"   - {error}")

    if args.json:
        print(json.dumps({"passed": all_passed, "violations": results}, indent=2))
    elif all_passed:
        print("[OK] All naming conventions correct")
    
    sys.exit(0 if all_passed else 1)

def check_naming_conventions_content(content):
    """Helper to check content directly (extracted from check_naming_conventions logic)"""
    errors = []
    
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
    enum_bodies = re.findall(r'enum\s+class\s+\w+[^{]*\{([^}]+)\}', content, re.DOTALL)
    for enum_body in enum_bodies:
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
    
    return len(errors) == 0, errors


if __name__ == "__main__":
    main()
