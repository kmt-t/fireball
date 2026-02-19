import os
import sys
import re
import argparse

# Force UTF-8 output for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

def summarize_file(file_path, root_dir):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        rel_path = os.path.relpath(file_path, root_dir).replace('\\', '/')
        print(f"\n# File: {rel_path}")
        
        lines = content.splitlines()
        
        # Regex (Simple approximation for definitions)
        # Class/Struct/Enum
        re_type = re.compile(r'^\s*(template\s*<.*?>\s*)?(class|struct|enum\s+class|enum)\s+(\w+)\s*\{?')
        
        # Function (ReturnType Name(Args))
        # Exclude control keywords
        keywords = {'if', 'while', 'for', 'switch', 'return', 'else', 'catch', 'bh_assert', 'LOG_VERBOSE', 'LOG_ERROR'}
        # Catch: Type Name(Args)
        re_func = re.compile(r'^\s*((?:[\w:<>*&]+\s+)+)(\w+)\s*\(([^)]*)\)')

        in_comment_block = False
        in_struct = False
        struct_indent = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Skip comments
            if stripped.startswith('//'):
                continue
            if '/*' in stripped:
                in_comment_block = True
            if '*/' in stripped:
                in_comment_block = False
                continue
            if in_comment_block:
                continue
            
            # Struct/Class member extraction (simple)
            if in_struct:
                if '}' in stripped and stripped.startswith('}'):
                    in_struct = False
                    continue
                # Simple member: Type Name; or Type *Name;
                member_match = re.match(r'^\s*((?:[\w:<>*&]+\s+)+)(\w+)(?:\[[^\]]*\])?\s*;', line)
                if member_match:
                    m_type = member_match.group(1).strip()
                    m_name = member_match.group(2)
                    print(f"    - {m_type} {m_name}")
                continue

            # Check Types
            match_type = re_type.match(line)
            if match_type:
                kind = match_type.group(2)
                name = match_type.group(3)
                print(f"  [{kind}] {name} (Line {i+1})")
                if '{' in line or (i+1 < len(lines) and '{' in lines[i+1]):
                    in_struct = True
                continue

            # Check Functions
            match_func = re_func.match(line)
            if match_func:
                ret_type = match_func.group(1).strip()
                name = match_func.group(2)
                args = match_func.group(3).strip()
                
                if name in keywords:
                    continue
                
                print(f"  [func] {ret_type} {name}({args}) (Line {i+1})")

    except Exception as e:
        print(f"Error reading {file_path}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Extract C++ symbols.")
    parser.add_argument("path", help="File or directory path")
    args = parser.parse_args()

    target_path = os.path.abspath(args.path)
    if not os.path.exists(target_path):
        print(f"Path not found: {target_path}")
        sys.exit(1)

    root_dir = os.getcwd()

    if os.path.isfile(target_path):
        summarize_file(target_path, root_dir)
    else:
        for root, dirs, files in os.walk(target_path):
            files.sort()
            for file in files:
                if file.endswith(('.cpp', '.cxx', '.h', '.hxx', '.hpp', '.c')):
                    summarize_file(os.path.join(root, file), root_dir)

if __name__ == "__main__":
    main()
