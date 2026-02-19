#!/usr/bin/env python3
import sys
import os
import subprocess
import argparse

def generate_report(file_path, search_dirs=None, include_paths=None, pipe_sources=False):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    explorer_sh = os.path.join(script_dir, "explore-codebase.sh")
    
    # Prepare arguments
    search_args = []
    if search_dirs:
        for d in search_dirs:
            search_args.extend(["--search-dir", d])
    
    if pipe_sources:
        search_args.append("--pipe-sources")
            
    include_args = []
    if include_paths:
        include_args.append("--")
        for i in include_paths:
            include_args.append(f"-I{i}")
    
    print(f"# Summary Report for {os.path.basename(file_path)}")
    
    print("\n## Module Structure (Graph)")
    # If pipe_sources is True, we need to pass the same stdin to cflow
    cmd_graph = ["bash", explorer_sh, "graph", file_path] + search_args
    subprocess.run(cmd_graph)
    
    print("\n## Key Symbols")
    cmd_symbols = ["bash", explorer_sh, "symbols", file_path] + include_args
    subprocess.run(cmd_symbols)

def main():
    parser = argparse.ArgumentParser(description="Generate a unified summary report (graph + symbols) for a module.")
    parser.add_argument("target", help="File or directory to report on")
    parser.add_argument("--search-dir", action="append", help="Directories to search for cflow (sources)")
    parser.add_argument("-I", "--include", action="append", help="Include paths for clang-check")
    parser.add_argument("--pipe-sources", action="store_true", help="Read source files from stdin for cflow")
    
    args = parser.parse_known_args()
    # parse_known_args returns (Namespace, list of extra)
    # But we want to handle the target explicitly
    
    # Re-parse to be cleaner
    args = parser.parse_args()
    
    target = args.target
    if os.path.isdir(target):
        # Batch process cxx files
        for root, _, files in os.walk(target):
            for f in files:
                if f.endswith(('.cxx', '.c', '.cpp')):
                    generate_report(os.path.join(root, f), search_dirs=args.search_dir, include_paths=args.include, pipe_sources=args.pipe_sources)
                    print("\n---\n")
    else:
        generate_report(target, search_dirs=args.search_dir, include_paths=args.include, pipe_sources=args.pipe_sources)

if __name__ == "__main__":
    main()
