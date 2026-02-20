#!/usr/bin/env python3
import sys
import re

def filter_cflow(input_lines):
    filtered = []
    # Patterns to exclude: std library, internal symbols (start with _ and not _ZN for mangled), etc.
    exclude_patterns = [
        r'^std::',
        r'^::std::',
        r'^__',
        r'^\s+\*',  # Sometimes cflow outputs info lines
        r'\(recursive\)'
    ]
    
    for line in input_lines:
        line = line.rstrip()
        if not line:
            continue
            
        # cflow format: "func() <...>" or "func(...) <...>"
        match = re.search(r'^(\s*)([\w:<>, ]+)\s*\(', line)
        if match:
            indent = match.group(1)
            func_name = match.group(2).strip()
            
            # Skip if matches any exclude pattern
            if any(re.search(pat, func_name) for pat in exclude_patterns) or func_name in ['if', 'for', 'while', 'switch']:
                continue
                
            # Skip if it's a very short line that doesn't look like a real function in our project
            if len(func_name) < 2 and func_name not in ['f']:
                continue
            
            # Clean up the line: keep only the initial part (indent + func name + ())
            line = f"{indent}{func_name}()"
        
        filtered.append(line)
    
    return filtered

def main():
    import stat
    import os
    def has_piped_input():
        if sys.stdin.isatty(): return False
        try:
            mode = os.fstat(0).st_mode
            return stat.S_ISFIFO(mode) or stat.S_ISREG(mode) or stat.S_ISSOCK(mode)
        except:
            return False

    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r') as f:
            lines = f.readlines()
    elif has_piped_input():
        lines = sys.stdin.readlines()
    else:
        print("Usage: graph_tool.py [file] (or pipe via stdin)")
        sys.exit(1)
        
    filtered_lines = filter_cflow(lines)
    for line in filtered_lines:
        print(line)

if __name__ == "__main__":
    main()
