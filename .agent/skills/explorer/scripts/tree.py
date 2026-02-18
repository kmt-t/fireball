#!/usr/bin/env python3
import os
import sys
import argparse

def print_tree(directory, prefix="", ignore_dirs=None, ignore_files=None):
    if ignore_dirs is None:
        ignore_dirs = {'.git', '.agent', '.roo', '.vscode', 'build', 'node_modules', '__pycache__'}
    if ignore_files is None:
        ignore_files = {'.gitignore', '.gitmodules', 'package-lock.json'}

    try:
        items = sorted(os.listdir(directory))
    except PermissionError:
        return

    # Filter items
    items = [i for i in items if i not in ignore_files]
    items = [i for i in items if not (os.path.isdir(os.path.join(directory, i)) and i in ignore_dirs)]

    for i, item in enumerate(items):
        path = os.path.join(directory, item)
        is_last = (i == len(items) - 1)
        connector = "└── " if is_last else "├── "
        
        print(f"{prefix}{connector}{item}")
        
        if os.path.isdir(path):
            extension = "    " if is_last else "│   "
            print_tree(path, prefix + extension, ignore_dirs, ignore_files)

def main():
    parser = argparse.ArgumentParser(description="Display directory tree.")
    parser.add_argument("path", nargs="?", default=".", help="Directory path")
    args = parser.parse_args()

    target = os.path.abspath(args.path)
    if not os.path.exists(target):
        print(f"Error: {target} does not exist.")
        sys.exit(1)

    print(os.path.basename(target) if os.path.basename(target) else target)
    print_tree(target)

if __name__ == "__main__":
    main()
