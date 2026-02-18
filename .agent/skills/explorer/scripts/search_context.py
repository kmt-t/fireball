#!/usr/bin/env python3
import os
import sys
import re
import argparse
from pathlib import Path

# Force UTF-8 output for Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def search_context(keyword, root_dir):
    print(f"--- 3-line Summary for: {keyword} ---")
    
    # 1. Definition (Search in inc/ and docs/)
    definition = "Not found"
    # Support both plain names and {Keyword} syntax
    keyword_esc = re.escape(keyword)
    def_pattern = re.compile(r'(?:^\s*(?:template\s*<.*?>\s*)?(?:class|struct|enum|resource|func|type)\s+' + keyword_esc + r'\b|' + keyword_esc + r'\s*[:=]|\|\s*`?' + keyword_esc + r'`?\s*\|)')
    wit_pattern = def_pattern # Unified
    
    search_paths = [Path(root_dir) / "inc", Path(root_dir) / "docs"]
    found_def = False
    
    for p in search_paths:
        if not p.exists(): continue
        for fpath in p.rglob("*"):
            if fpath.is_file() and fpath.suffix in ['.hxx', '.hxx', '.h', '.hpp', '.md', '.wit']:
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f):
                            if def_pattern.search(line) or (fpath.suffix == '.wit' and wit_pattern.search(line)):
                                rel = fpath.relative_to(root_dir).as_posix()
                                definition = f"[{rel}:{i+1}] {line.strip()}"
                                found_def = True
                                break
                except: pass
            if found_def: break
        if found_def: break
    print(f"1. Definition: {definition}")

    # 2. Usage (Search in src/ and docs/)
    usages = []
    keyword_esc = re.escape(keyword)
    usage_pattern = re.compile(keyword_esc)
    search_paths = [Path(root_dir) / "src", Path(root_dir) / "docs/components"]
    
    count = 0
    for p in search_paths:
        if not p.exists(): continue
        for fpath in p.rglob("*"):
            if fpath.is_file() and fpath.suffix in ['.cxx', '.c', '.cpp', '.md']:
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f):
                            if usage_pattern.search(line) and "Definition" not in line:
                                rel = fpath.relative_to(root_dir).as_posix()
                                usages.append(f"[{rel}:{i+1}] {line.strip()}")
                                count += 1
                                if count >= 2: break
                except: pass
            if count >= 2: break
        if count >= 2: break
    
    usage_str = "; ".join(usages) if usages else "Not found"
    print(f"2. Usage: {usage_str}")

    # 3. Intent/Context (Search in docs/ or comments near definition)
    intent = "Seek design docs for details."
    doc_path = Path(root_dir) / "docs"
    found_intent = False
    
    # Try to find a line with the keyword and some descriptive text in docs
    for fpath in doc_path.rglob("*.md"):
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                # Look for lines like "Keyword is ..." or "The goal of Keyword is ..."
                match = re.search(r'(?:#+.*' + re.escape(keyword) + r'.*?\n)(.*?)(?:\n\n|#)', content, re.IGNORECASE | re.DOTALL)
                if match:
                    intent = match.group(1).replace('\n', ' ').strip()[:100] + "..."
                    found_intent = True
                    break
        except: pass
        if found_intent: break
        
    print(f"3. Intent: {intent}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a 3-line summary for a keyword.")
    parser.add_argument("keyword", help="The keyword to search for.")
    args = parser.parse_args()
    
    search_context(args.keyword, os.getcwd())
