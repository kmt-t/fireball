#!/usr/bin/env python3
import re
import sys
import os
import argparse

# Coding style rules based on cpp_coding_style.md
# Rule: snake_case for names, UPPER_SNAKE_CASE for constants/macros
# Rule: class_member_, class_static_member__
# Rule: K&R braces, 2-space indent, max 100 chars

import subprocess

import shutil

def run_command(cmd, filepath):
    if not shutil.which(cmd[0]):
        return None, None
    try:
        result = subprocess.run(cmd + [filepath], capture_output=True, text=True)
        return result.stdout + result.stderr, result.returncode
    except FileNotFoundError:
        return None, None

class Linter:
    def __init__(self, filepath):
        self.filepath = filepath
        self.violations = []
        self.in_class = False

    def report(self, line_num, msg):
        self.violations.append((line_num, msg))

    def check(self):
        # 1. Try clang-format
        out, code = run_command(["clang-format", "--dry-run", "--Werror"], self.filepath)
        if out is not None and code != 0:
            self.report(0, f"Clang-format violations:\n{out}")

        # 2. Try clang-tidy
        out, code = run_command(["clang-tidy", "--quiet"], self.filepath)
        if out is not None and code != 0:
            self.report(0, f"Clang-tidy violations:\n{out}")

        # 3. Fallback/Complementary Python checks
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line_num, line in enumerate(lines, 1):
                    self._check_line_length(line_num, line)
                    self._check_indentation(line_num, line)
                    self._check_naming(line_num, line)
                    self._track_context(line)
        except Exception as e:
            print(f"Error reading {self.filepath}: {e}")
        return self.violations

    def _check_line_length(self, line_num, line):
        if len(line.rstrip('\n')) > 100:
            self.report(line_num, f"Line too long ({len(line.rstrip('\n'))} > 100 chars)")

    def _check_indentation(self, line_num, line):
        stripped = line.lstrip()
        if stripped and not stripped.startswith('*'): 
            indent = len(line) - len(stripped)
            if indent % 2 != 0:
                self.report(line_num, f"Indent is not a multiple of 2 ({indent})")
            if '\t' in line:
                self.report(line_num, "Tabs are used for indentation")

    def _check_naming(self, line_num, line):
        if self.in_class:
            member_match = re.search(r'\b[a-zA-Z0-9_<>: ]+\b\s+([a-z][a-z0-9_]*);\s*(?://.*)?$', line)
            if member_match:
                m_name = member_match.group(1)
                if not m_name.endswith('_'):
                    self.report(line_num, f"Class member '{m_name}' should have a trailing underscore")

        if re.search(r'\b[a-z]+[A-Z][a-z0-9]+\b', line):
            if not any(x in line for x in ["clang-format", "nolint", "NOLINT"]):
                self.report(line_num, "Potential camelCase detected. Use snake_case.")

    def _track_context(self, line):
        if 'class ' in line and '{' in line:
            self.in_class = True
        elif 'struct ' in line and '{' in line:
            self.in_class = False
        if '};' in line:
            self.in_class = False

def main():
    parser = argparse.ArgumentParser(description="Lint C++ code for Fireball coding style")
    parser.add_argument("paths", nargs="+", help="Files or directories to lint")
    parser.add_argument("--recursive", "-r", action="store_true", help="Search directories recursively")
    args = parser.parse_args()

    all_violations = 0
    for path in args.paths:
        if os.path.isfile(path):
            if path.endswith(('.hxx', '.cxx', '.cpp', '.h')):
                linter = Linter(path)
                v = linter.check()
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
                        linter = Linter(file_path)
                        v = linter.check()
                        if v:
                            print(f"--- {file_path} ---")
                            for line, msg in v:
                                print(f"Line {line}: {msg}")
                            all_violations += len(v)
    
    if all_violations == 0:
        print("Coding style: OK")
        sys.exit(0)
    else:
        print(f"\nTotal style violations: {all_violations}")
        sys.exit(1)

if __name__ == "__main__":
    main()
