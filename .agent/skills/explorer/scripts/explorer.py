#!/usr/bin/env python3
import os
import sys
import re
import subprocess
import json
import argparse

# Set encoding for Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

class FireballExplorer:
    def __init__(self, root_dir, json_mode=False):
        self.root_dir = os.path.abspath(root_dir)
        self.cwd = self.root_dir
        self.json_mode = json_mode

    def run_interactive(self):
        while True:
            self.clear_screen()
            print("=" * 60)
            print(f" Fireball Explorer - CWD: {os.path.relpath(self.cwd, self.root_dir)}")
            print("=" * 60)
            
            items = self.list_items()
            self.display_menu(items)
            
            choice = input("\nSelect an item (number), '..' to go up, or 'q' to quit: ").strip()
            
            if choice.lower() == 'q':
                break
            elif choice == '..':
                self.cwd = os.path.dirname(self.cwd)
                if not self.cwd.startswith(self.root_dir):
                    self.cwd = self.root_dir
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(items):
                    item = items[idx]
                    path = os.path.join(self.cwd, item)
                    if os.path.isdir(path):
                        self.cwd = path
                    else:
                        self.inspect_file(path)
                else:
                    print("Invalid choice.")
                    input("Press Enter to continue...")

    def clear_screen(self):
        if not self.json_mode:
            os.system('cls' if os.name == 'nt' else 'clear')

    def list_items(self):
        try:
            return sorted(os.listdir(self.cwd))
        except Exception as e:
            if not self.json_mode:
                print(f"Error: {e}")
            return []

    def display_menu(self, items):
        for i, item in enumerate(items):
            prefix = "[D]" if os.path.isdir(os.path.join(self.cwd, item)) else "[F]"
            print(f"{i+1:3}. {prefix} {item}")

    def inspect_file(self, file_path):
        while True:
            self.clear_screen()
            print(f"--- File: {os.path.relpath(file_path, self.root_dir)} ---")
            
            # Show traceability keywords automatically
            keywords = self.extract_traceability_keywords(file_path)
            if keywords:
                print(f"Keywords: {', '.join(keywords)}")
            
            print("\n1. Summarize (Headers/Symbols)")
            print("2. Search Callers (Grep)")
            print("3. Search Callees (Regex-based)")
            print("4. AST Struct Dump (Docker/Clang)")
            print("5. Context Summary (3-line)")
            print("b. Back")
            
            choice = input("\nSelect action: ").strip()
            
            if choice.lower() == 'b':
                break
            elif choice == '1':
                self.summarize_file(file_path)
            elif choice == '2':
                funcs = self.get_functions_in_file(file_path)
                target = self.pick_one(funcs, "Select function to find callers")
                if target:
                    self.search_callers(target)
            elif choice == '3':
                self.search_callees(file_path)
            elif choice == '4':
                self.ast_dump(file_path)
            elif choice == '5':
                self.run_context_summary(file_path)
            
            input("\nPress Enter to continue...")

    def get_functions_in_file(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                funcs = re.findall(r'(?:[\w:<>*&]+\s+)+(\w+)\s*\(', content)
                return sorted(list(set([f for f in funcs if f not in {'if', 'while', 'for', 'switch', 'return', 'else', 'catch', 'bh_assert'}])))
        except:
            return []

    def summarize_file(self, file_path):
        ext = os.path.splitext(file_path)[1]
        script = ""
        if ext == ".md":
            script = "summarize_markdown.py"
        elif ext in [".c", ".cpp", ".cxx", ".h", ".hpp", ".hxx"]:
            script = "summarize_code.py"
        
        if script:
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
            res = subprocess.run([sys.executable, script_path, file_path], capture_output=True, text=True)
            if self.json_mode:
                return res.stdout
            else:
                print(res.stdout)
        else:
            if self.json_mode:
                return f"No summary tool for {ext}"
            else:
                print(f"No summary tool for {ext}")

    def pick_one(self, items, prompt):
        for i, item in enumerate(items):
            print(f"{i+1:3}. {item}")
        choice = input(f"\n{prompt} (or enter name): ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx]
        return choice if choice else None

    def search_callers(self, target_func, depth=1, current_depth=0):
        if current_depth >= depth:
            return
        
        indent = "  " * current_depth
        if not self.json_mode:
            print(f"{indent}Finding callers of '{target_func}' (Depth {current_depth+1})...")
        
        callers = []
        # Native Python search instead of grep for cross-platform reliability
        search_dirs = [os.path.join(self.root_dir, d) for d in ['src', 'inc', 'docs/references/wamr/core']]
        search_dirs = [d for d in search_dirs if os.path.exists(d)]
        
        if not search_dirs:
            search_dirs = [self.root_dir]

        pattern = re.compile(r'\b' + re.escape(target_func) + r'\b')

        for sdir in search_dirs:
            for root, dirs, files in os.walk(sdir):
                if '.agent' in root or '.git' in root:
                    continue
                for fname in files:
                    if fname.endswith(('.c', '.cpp', '.cxx', '.h', '.hpp', '.hxx', '.md')):
                        fpath = os.path.join(root, fname)
                        try:
                            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                                for i, line in enumerate(f):
                                    if pattern.search(line):
                                        lnum = i + 1
                                        func_name = self.find_enclosing_function(fpath, lnum)
                                        if func_name and func_name != target_func:
                                            callers.append((func_name, fpath, lnum))
                        except:
                            pass
        
        unique_callers = sorted(list(set(callers)))
        
        if self.json_mode:
            if current_depth == 0:
                 # In batch mode, we return the results for the first level
                 return unique_callers
        
        for name, fpath, lnum in unique_callers:
            rel_fpath = os.path.relpath(fpath, self.root_dir).replace('\\', '/')
            if not self.json_mode:
                print(f"{indent}<- {name} ({rel_fpath}:{lnum})")
            if depth > 1:
                self.search_callers(name, depth, current_depth + 1)
        
        if self.json_mode and current_depth == 0:
            print(json.dumps(unique_callers, indent=2))

    def find_enclosing_function(self, file_path, line_num):
        # Very simple heuristic: find the last Line that looks like a function definition before line_num
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            for i in range(line_num - 1, -1, -1):
                m = re.match(r'^\s*((?:[\w:<>*&]+\s+)+)(\w+)\s*\(', lines[i])
                if m:
                    name = m.group(2)
                    if name not in {'if', 'while', 'for', 'switch', 'return', 'else', 'catch', 'bh_assert'}:
                        return name
        except:
            pass
        return None

    def search_callees(self, file_path):
        if not self.json_mode:
            print(f"\nPotential calls made in {os.path.basename(file_path)}:")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                calls = re.findall(r'(\w+)\s*\(', content)
                keywords = {'if', 'for', 'while', 'switch', 'return', 'sizeof', 'bh_assert'}
                unique_calls = sorted(list(set([c for c in calls if c not in keywords])))
                if self.json_mode:
                    return unique_calls
                for c in unique_calls:
                    print(f"  - {c}")
        except Exception as e:
            if not self.json_mode:
                print(f"Error: {e}")

    def ast_dump(self, file_path):
        rel_path = os.path.relpath(file_path, self.root_dir).replace('\\', '/')
        if not self.json_mode:
            print(f"\nRunning native clang AST dump for {rel_path}...")
        
        # In-container include paths
        include_dirs = [
            "docs/references/wamr/core/iwasm/include",
            "docs/references/wamr/core/shared/utils",
            "docs/references/wamr/core/iwasm/fast-jit",
            "inc"
        ]
        includes = " ".join([f"-I{d}" for d in include_dirs])
        
        # Use native 'clang' directly. We assume we are in the container.
        cmd = f"clang -Xclang -ast-dump -fsyntax-only {includes} {rel_path} 2>/dev/null | grep -E 'RecordDecl|TypedefDecl|FieldDecl|EnumDecl'"
        
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if self.json_mode:
            return res.stdout
        else:
            print(res.stdout)

    def run_context_summary(self, file_path):
        keywords = self.extract_traceability_keywords(file_path)
        if not keywords:
            print("No keywords found in file.")
            return
        
        target = self.pick_one(keywords, "Select keyword for context summary")
        if target:
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_context.py")
            subprocess.run([sys.executable, script_path, target])

    def extract_traceability_keywords(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                return sorted(list(set(re.findall(r'\{([A-Z][A-Za-z0-9_]+)\}', content))))
        except:
            return []

    def batch_summary(self, path):
        data = {
            "path": os.path.relpath(path, self.root_dir).replace('\\', '/'),
            "type": "directory" if os.path.isdir(path) else "file",
            "keywords": self.extract_traceability_keywords(path) if os.path.isfile(path) else []
        }
        if os.path.isfile(path):
            data["summary"] = self.summarize_file(path)
            data["functions"] = self.get_functions_in_file(path)
        print(json.dumps(data, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fireball Explorer CLI")
    parser.add_argument("path", nargs="?", default=".", help="Starting path")
    parser.add_argument("--json", action="store_true", help="Output JSON and exit")
    parser.add_argument("--summary", action="store_true", help="Summary of file/dir")
    parser.add_argument("--callers", help="Find callers of specified function")
    parser.add_argument("--depth", type=int, default=1, help="Depth for recursive search")
    parser.add_argument("--ast", action="store_true", help="Run AST dump on file")
    args = parser.parse_args()

    root = os.getcwd()
    explorer = FireballExplorer(root, json_mode=args.json)
    
    if args.json or args.summary or args.callers or args.ast:
        target = os.path.abspath(args.path)
        if args.summary:
            explorer.batch_summary(target)
        elif args.callers:
            explorer.search_callers(args.callers, depth=args.depth)
        elif args.ast:
            explorer.ast_dump(target)
    else:
        explorer.run_interactive()
