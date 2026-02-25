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
import re
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
    """Check a single C++ file for violations. Returns list of violations."""
    violations_found = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            full_content = f.read()
            
            # Remove comments to avoid false positives in documentation
            content = re.sub(r'/\*.*?\*/', '', full_content, flags=re.DOTALL)
            content = re.sub(r'//.*', '', content)
            
            for allowed in ALLOWED_EXCEPTIONS:
                content = content.replace(allowed, "")
            
            for pattern, message in VIOLATIONS.items():
                if re.search(pattern, content):
                    violations_found.append(message)
    
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return [f"Error reading file: {e}"]
    
    return violations_found


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Check coding violations in C++ files.")
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
            violations = get_content_violations(content)
            passed = len(violations) == 0
            if args.json:
                print(json.dumps({"passed": passed, "violations": violations}, indent=2))
            else:
                if passed:
                    print("[OK] No violations found")
                else:
                    print("[ERROR] Violations detected")
                    for v in violations:
                        print(f"   - {v}")
            sys.exit(0 if passed else 1)
        else:
            parser.print_help()
            sys.exit(1)
    
    if not args.json: print(f"[*] Checking coding violations...")
    
    all_results = []
    total_passed = True
    
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
            violations = check_file(f)
            if violations:
                total_passed = False
                all_results.append({"file": str(f), "violations": violations})
                if not args.json:
                    print(f"[ERROR] {f}:")
                    for v in violations:
                        print(f"   - {v}")

    if args.json:
        print(json.dumps({"passed": total_passed, "results": all_results}, indent=2))
    elif total_passed:
        print("[OK] No violations found")
    
    sys.exit(0 if total_passed else 1)

def get_content_violations(content):
    """Helper to check content directly"""
    violations_found = []
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    content = re.sub(r'//.*', '', content)
    for allowed in ALLOWED_EXCEPTIONS:
        content = content.replace(allowed, "")
    for pattern, message in VIOLATIONS.items():
        if re.search(pattern, content):
            violations_found.append(message)
    return violations_found

def check_content(content):
    """Helper to check content directly (extracted from check_file logic)"""
    violations_found = []
    # Remove comments
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    content = re.sub(r'//.*', '', content)
    for allowed in ALLOWED_EXCEPTIONS:
        content = content.replace(allowed, "")
    for pattern, message in VIOLATIONS.items():
        if re.search(pattern, content):
            violations_found.append(message)
    if violations_found:
        for msg in violations_found:
            print(f"   - {msg}")
        return False
    return True


if __name__ == "__main__":
    main()
