#!/usr/bin/env python3
import re
import sys
import os
import argparse

# Rules based on cpp_embedded/SKILL.md
FORBIDDEN_INCLUDES = [
    r'#include\s+<vector>',
    r'#include\s+<map>',
    r'#include\s+<unordered_map>',
    r'#include\s+<list>',
    r'#include\s+<deque>',
    r'#include\s+<set>',
    r'#include\s+<unordered_set>',
    r'#include\s+<string>',
    r'#include\s+<iostream>',
    r'#include\s+<fstream>',
    r'#include\s+<thread>',
    r'#include\s+<future>',
    r'#include\s+<exception>'
]

FORBIDDEN_PATTERNS = [
    (r'\bstd::vector\b', "Use std::array or std::span instead"),
    (r'\bstd::map\b', "Use Sorted Indexed Array pattern instead"),
    (r'\bstd::unordered_map\b', "Avoid hash maps due to heap allocation"),
    (r'\bstd::unique_ptr\b', "Use static lifecycle or custom Ref instead"),
    (r'\bstd::shared_ptr\b', "Use static lifecycle or custom Ref instead"),
    (r'\bstd::function\b', "Use economic_function instead"),
    (r'\bmalloc\b', "Use partition allocation or RAII"),
    (r'\bfree\b', "Use RAII/Destructors for resource management"),
    (r'\bnew\b(?!\s*\(.*?\)\s*)', "Avoid global new (placement new is allowed)"),
    (r'\bdelete\b', "Avoid manual delete, use RAII")
]

def check_file(filepath):
    violations = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line_num, line in enumerate(lines, 1):
                # Check includes
                for pattern in FORBIDDEN_INCLUDES:
                    if re.search(pattern, line):
                        violations.append((line_num, f"Forbidden include: {pattern}"))
                
                # Check patterns
                for pattern, msg in FORBIDDEN_PATTERNS:
                    if re.search(pattern, line):
                        # Simple exclude for comments (primitive)
                        if line.strip().startswith('//') or '/*' in line:
                             continue
                        violations.append((line_num, f"Forbidden pattern '{pattern}': {msg}"))
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
    
    return violations

def main():
    parser = argparse.ArgumentParser(description="Check C++ code for embedded rule violations")
    parser.add_argument("paths", nargs="+", help="Files or directories to check")
    parser.add_argument("--recursive", "-r", action="store_true", help="Search directories recursively")
    args = parser.parse_args()

    all_violations = 0
    for path in args.paths:
        if os.path.isfile(path):
            if path.endswith(('.hxx', '.cxx', '.cpp', '.h')):
                v = check_file(path)
                if v:
                    print(f"--- {path} ---")
                    for line, msg in v:
                        print(f"Line {line}: {msg}")
                    all_violations += len(v)
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                if not args.recursive and root != path:
                    continue
                for file in files:
                    if file.endswith(('.hxx', '.cxx', '.cpp', '.h')):
                        file_path = os.path.join(root, file)
                        v = check_file(file_path)
                        if v:
                            print(f"--- {file_path} ---")
                            for line, msg in v:
                                print(f"Line {line}: {msg}")
                            all_violations += len(v)
    
    if all_violations == 0:
        print("No violations found.")
        sys.exit(0)
    else:
        print(f"\nTotal violations: {all_violations}")
        sys.exit(1)

if __name__ == "__main__":
    main()
