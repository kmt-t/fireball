import os
import sys
import re
import argparse
import json

# Force UTF-8 output for Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def extract_markdown_decorated_items(line):
    # {Keyword}, **Bold**, __Bold__, `Code`
    patterns = {
        'keyword': r'\{([^}]+)\}',
        'bold': r'\*\*(.*?)\*\*|__(.*?)__',
        'code': r'`(.*?)`'
    }
    
    items = []
    # Keywords
    for match in re.finditer(patterns['keyword'], line):
        items.append({'type': 'keyword', 'value': match.group(1)})
    
    # Bold (handle both ** and __)
    for match in re.finditer(patterns['bold'], line):
        val = match.group(1) or match.group(2)
        if val:
            items.append({'type': 'bold', 'value': val})
            
    # Code
    for match in re.finditer(patterns['code'], line):
        items.append({'type': 'code', 'value': match.group(1)})
        
    return items

def summarize_markdown(file_path, root_dir, output_json=False):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        rel_path = os.path.relpath(file_path, root_dir).replace('\\', '/')
        
        summary = {
            'file': rel_path,
            'type': 'markdown',
            'structure': []
        }
        
        current_section = None
        in_code_block = False
        
        for line in lines:
            line_content = line.strip()
            if line_content.startswith('```'):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            
            # Check for headers
            if line_content.startswith('#'):
                m = re.match(r'^(#+)\s+(.*)', line_content)
                if m:
                    level = len(m.group(1))
                    title = m.group(2)
                    section = {
                        'level': level,
                        'title': title,
                        'items': []
                    }
                    summary['structure'].append(section)
                    current_section = section
                    
                    # Also check title for keywords
                    decorated = extract_markdown_decorated_items(title)
                    if decorated:
                        section['items'].extend(decorated)
                continue
            
            # Check for decorated items in regular lines
            decorated = extract_markdown_decorated_items(line_content)
            if decorated and current_section:
                current_section['items'].extend(decorated)

        if output_json:
            print(json.dumps(summary, ensure_ascii=False))
        else:
            print(f"\n# File: {rel_path} (Markdown)")
            for sec in summary['structure']:
                indent = "  " * (sec['level'] - 1)
                print(f"{indent}- {sec['title']}")
                for item in sec['items']:
                    print(f"{indent}  * [{item['type']}] {item['value']}")

    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)

def summarize_code(file_path, root_dir, output_json=False):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        rel_path = os.path.relpath(file_path, root_dir).replace('\\', '/')
        
        summary = {
            'file': rel_path,
            'type': 'code',
            'symbols': []
        }
        
        lines = content.splitlines()
        
        # Regex (Simple approximation for definitions)
        re_type = re.compile(r'^\s*(template\s*<.*?>\s*)?(class|struct|enum\s+class|enum)\s+(\w+)\s*\{?')
        keywords = {'if', 'while', 'for', 'switch', 'return', 'else', 'catch', 'bh_assert', 'LOG_VERBOSE', 'LOG_ERROR'}
        re_func = re.compile(r'^\s*((?:[\w:<>*&]+\s+)+)(\w+)\s*\(([^)]*)\)')

        in_comment_block = False
        in_struct = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('//'): continue
            if '/*' in stripped: in_comment_block = True
            if '*/' in stripped:
                in_comment_block = False
                continue
            if in_comment_block: continue
            
            if in_struct:
                if '}' in stripped and stripped.startswith('}'):
                    in_struct = False
                    continue
                member_match = re.match(r'^\s*((?:[\w:<>*&]+\s+)+)(\w+)(?:\[[^\]]*\])?\s*;', line)
                if member_match:
                    summary['symbols'].append({'type': 'member', 'value': f"{member_match.group(1).strip()} {member_match.group(2)}", 'line': i+1})
                continue

            match_type = re_type.match(line)
            if match_type:
                kind = match_type.group(2)
                name = match_type.group(3)
                summary['symbols'].append({'type': kind, 'value': name, 'line': i+1})
                if '{' in line or (i+1 < len(lines) and '{' in lines[i+1]):
                    in_struct = True
                continue

            match_func = re_func.match(line)
            if match_func:
                ret_type = match_func.group(1).strip()
                name = match_func.group(2)
                args = match_func.group(3).strip()
                if name not in keywords:
                    summary['symbols'].append({'type': 'function', 'value': f"{ret_type} {name}({args})", 'line': i+1})

        if output_json:
            print(json.dumps(summary, ensure_ascii=False))
        else:
            print(f"\n# File: {rel_path} (Code)")
            for sym in summary['symbols']:
                print(f"  [{sym['type']}] {sym['value']} (Line {sym['line']})")

    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="Unified summarizer for code and markdown.")
    parser.add_argument("path", help="File or directory path")
    parser.add_argument("--json", "-j", action="store_true", help="Output in JSON format")
    args = parser.parse_args()

    target_path = os.path.abspath(args.path)
    if not os.path.exists(target_path):
        print(f"Path not found: {target_path}", file=sys.stderr)
        sys.exit(1)

    root_dir = os.getcwd()

    def process_file(fpath):
        if fpath.endswith('.md'):
            summarize_markdown(fpath, root_dir, args.json)
        elif fpath.endswith(('.cpp', '.cxx', '.h', '.hxx', '.hpp', '.c')):
            summarize_code(fpath, root_dir, args.json)

    if os.path.isfile(target_path):
        process_file(target_path)
    else:
        for root, dirs, files in os.walk(target_path):
            files.sort()
            for file in files:
                process_file(os.path.join(root, file))

if __name__ == "__main__":
    main()
