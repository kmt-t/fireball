#!/usr/bin/env python3
import re
import sys
import os
import argparse

# Rules based on embedded_cpp_check/SKILL.md
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
                        # Simple exclude for comments (primitive but better)
                        trimmed = line.strip()
                        if trimmed.startswith('//') or trimmed.startswith('*') or '/*' in line:
                             continue
                        violations.append((line_num, f"Forbidden pattern '{pattern}': {msg}"))
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
    
    return violations

def main():
    parser = argparse.ArgumentParser(description="Check C++ code for embedded rule violations")
    parser.add_argument("paths", nargs="*", help="Files or directories to check")
    parser.add_argument("--recursive", "-r", action="store_true", help="Search directories recursively")
    parser.add_argument("--stdin-paths", "-p", action="store_true", help="Read target paths from STDIN")
    parser.add_argument("--json", "-j", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    targets = []
    
    # Support stdin paths
    if args.stdin_paths:
        for line in sys.stdin:
            path = line.strip()
            if path:
                targets.append(path)
    
    # Support command line paths
    if args.paths:
        targets.extend(args.paths)
        
    if not targets:
        # If no paths and not explicitly reading from STDIN, print help
        if not args.stdin_paths:
            parser.print_help()
            sys.exit(1)

    results = []
    total_violations = 0

    def process_path(path):
        nonlocal total_violations
        if os.path.isfile(path):
            if path.endswith(('.hxx', '.cxx', '.cpp', '.h')):
                v = check_file(path)
                if v:
                    results.append({"file": path, "violations": [{"line": ln, "message": m} for ln, m in v]})
                    total_violations += len(v)
        elif os.path.isdir(path):
            for root, _, files in os.walk(path):
                if not args.recursive and root != path:
                    continue
                for file in files:
                    if file.endswith(('.hxx', '.cxx', '.cpp', '.h')):
                        file_path = os.path.join(root, file)
                        v = check_file(file_path)
                        if v:
                            results.append({"file": file_path, "violations": [{"line": ln, "message": m} for ln, m in v]})
                            total_violations += len(v)
        else:
            if not args.json:
                print(f"Warning: Path not found or not accessible: {path}", file=sys.stderr)

    for path in targets:
        process_path(path)
    
    if args.json:
        import json
        print(json.dumps({"total_violations": total_violations, "files": results}, indent=2))
    else:
        if total_violations == 0:
            print("No violations found.")
            sys.exit(0)
        else:
            for item in results:
                print(f"--- {item['file']} ---")
                for v in item['violations']:
                    print(f"Line {v['line']}: {v['message']}")
            print(f"\nTotal violations: {total_violations}")
    
    sys.exit(1 if total_violations > 0 else 0)

if __name__ == "__main__":
    main()
