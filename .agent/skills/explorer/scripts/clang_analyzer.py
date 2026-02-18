#!/usr/bin/env python3
import os
import sys
import subprocess
import re
import argparse

class ClangAnalyzer:
    def __init__(self, include_dirs=None):
        self.include_dirs = include_dirs or []

    def get_ast_dump(self, file_path):
        includes = " ".join([f"-I{d}" for d in self.include_dirs])
        # Force a generic target if possible or just rely on local clang
        cmd = f"clang -Xclang -ast-dump -fsyntax-only {includes} {file_path} 2>/dev/null"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return res.stdout

    def parse_ast(self, dump_text):
        symbols = []
        current_func = None
        
        # Regex patterns for parsing clang ast-dump
        # Example: |-CXXRecordDecl 0x... <line:12:1, line:15:1> line:12:7 struct Foo definition
        re_decl = re.compile(r'^(?:\|-|`-)([A-Za-z]+Decl)\s+0x[0-9a-f]+\s+<[^>]+>\s+(?:line|col):(\d+)(?::\d+)?\s+(?:(?:(?:\w+)\s+)?(\w+))')
        # Example: | `-CallExpr 0x... <line:20:5, col:15> 'int'
        #          |   `-ImplicitCastExpr ...
        #          |     `-DeclRefExpr 0x... <col:5> 'int (int)' Function 0x... 'bar' 'int (int)'
        re_call = re.compile(r'DeclRefExpr\s+0x[0-9a-f]+\s+<[^>]+>\s+Function\s+0x[0-9a-f]+\s+\'(\w+)\'')

        lines = dump_text.splitlines()
        for i, line in enumerate(lines):
            decl_match = re_decl.search(line)
            if decl_match:
                kind = decl_match.group(1)
                line_num = decl_match.group(2)
                name = decl_match.group(3)
                
                if kind in ('FunctionDecl', 'CXXMethodDecl'):
                    current_func = name
                    symbols.append({"kind": "func", "name": name, "line": line_num, "calls": []})
                elif kind in ('CXXRecordDecl', 'RecordDecl', 'EnumDecl'):
                    if 'definition' in line:
                        symbols.append({"kind": "type", "name": name, "line": line_num})
            
            call_match = re_call.search(line)
            if call_match and current_func:
                callee = call_match.group(1)
                # Avoid self-recursion or duplicates in simple list
                if symbols and symbols[-1]["kind"] == "func" and symbols[-1]["name"] == current_func:
                    if callee not in symbols[-1]["calls"]:
                        symbols[-1]["calls"].append(callee)

        return symbols

    def analyze(self, file_path):
        if not os.path.exists(file_path):
            return None
        
        dump = self.get_ast_dump(file_path)
        return self.parse_ast(dump)

def main():
    parser = argparse.ArgumentParser(description="Clang-based AST Analyzer")
    parser.add_argument("file", help="Source file to analyze")
    parser.add_argument("-I", "--include", action="append", help="Include directory")
    args = parser.parse_args()

    # No defaults - rely on command-line arguments
    includes = (args.include or [])

    analyzer = ClangAnalyzer(includes)
    results = analyzer.analyze(args.file)

    if results:
        print(f"\n# Symbols in {args.file}:")
        for sym in results:
            if sym["kind"] == "type":
                print(f"  [type] {sym['name']} (Line {sym['line']})")
            elif sym["kind"] == "func":
                print(f"  [func] {sym['name']} (Line {sym['line']})")
                if sym["calls"]:
                    print(f"    -> calls: {', '.join(sym['calls'])}")
    else:
        print("Analysis failed or no symbols found.")

if __name__ == "__main__":
    main()
