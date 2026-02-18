import os
import sys
import argparse

# Force UTF-8 output for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import re
import json

def extract_decorated_items(line):
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

def summarize_file(file_path, root_dir, output_json=False):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        rel_path = os.path.relpath(file_path, root_dir).replace('\\', '/')
        
        summary = {
            'file': rel_path,
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
                    decorated = extract_decorated_items(title)
                    if decorated:
                        section['items'].extend(decorated)
                continue
            
            # Check for decorated items in regular lines
            decorated = extract_decorated_items(line_content)
            if decorated and current_section:
                current_section['items'].extend(decorated)

        if output_json:
            print(json.dumps(summary, ensure_ascii=False))
        else:
            print(f"\n# File: {rel_path}")
            for sec in summary['structure']:
                indent = "  " * (sec['level'] - 1)
                print(f"{indent}- {sec['title']}")
                for item in sec['items']:
                    print(f"{indent}  * [{item['type']}] {item['value']}")

    except Exception as e:
        print(f"Error reading {file_path}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Extract markdown headers and keywords.")
    parser.add_argument("path", help="File or directory path")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    args = parser.parse_args()

    target_path = os.path.abspath(args.path)
    if not os.path.exists(target_path):
        print(f"Path not found: {target_path}")
        sys.exit(1)

    root_dir = os.getcwd()

    if os.path.isfile(target_path):
        summarize_file(target_path, root_dir, args.json)
    else:
        for root, dirs, files in os.walk(target_path):
            for file in files:
                if file.endswith('.md'):
                    summarize_file(os.path.join(root, file), root_dir, args.json)

if __name__ == "__main__":
    main()
