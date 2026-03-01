#!/usr/bin/env python3
import sys
import os
import subprocess
import argparse

def generate_report(file_path, search_dirs=None, include_paths=None, stdin_paths=False):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    explorer_py = os.path.join(script_dir, "explore_codebase.py")
    
    # Prepare arguments
    search_args = []
    if search_dirs:
        for d in search_dirs:
            search_args.extend(["--search-dir", d])
    
    if stdin_paths:
        search_args.append("--stdin-paths")
            
    include_args = []
    if include_paths:
        for i in include_paths:
            include_args.extend(["--include", i])
    
    print(f"# Summary Report for {os.path.basename(file_path)}")
    
    print("\n## Module Structure (Graph)")
    cmd_graph = [sys.executable, explorer_py, "--graph", file_path] + search_args
    subprocess.run(cmd_graph)
    
    print("\n## Key Symbols")
    cmd_symbols = [sys.executable, explorer_py, "--symbols", file_path] + include_args
    subprocess.run(cmd_symbols)

def main():
    parser = argparse.ArgumentParser(description="Generate a unified summary report (graph + symbols) for a module.")
    parser.add_argument("target", help="File or directory to report on")
    parser.add_argument("--search-dir", action="append", help="Directories to search for cflow (sources)")
    parser.add_argument("-I", "--include", action="append", help="Include paths for clang-check")
    parser.add_argument("--stdin-paths", "-p", action="store_true", help="Read target files or source items from STDIN")
    
    args = parser.parse_args()
    
    target = args.target
    if os.path.isdir(target):
        # Batch process cxx files
        for root, _, files in os.walk(target):
            for f in files:
                if f.endswith(('.cxx', '.c', '.cpp')):
                    generate_report(os.path.join(root, f), search_dirs=args.search_dir, include_paths=args.include, stdin_paths=args.stdin_paths)
                    print("\n---\n")
    else:
        generate_report(target, search_dirs=args.search_dir, include_paths=args.include, stdin_paths=args.stdin_paths)

if __name__ == "__main__":
    main()
