import os
import re
import sys
import argparse
import difflib
from datetime import datetime

# Force UTF-8 output for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

def load_official_keywords(req_list_path):
    keywords = set()
    try:
        with open(req_list_path, 'r', encoding='utf-8') as f:
            for line in f:
                # Match {Keyword} in the list (allows with or without backticks)
                match = re.search(r'\{([a-zA-Z0-9_]+)\}', line)
                if match:
                    keywords.add(match.group(1))
    except Exception as e:
        print(f"Error loading keywords from {req_list_path}: {e}")
        sys.exit(1)
    return keywords

def scan_file_for_potential_keywords(file_path):
    potential_keywords = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        in_code_block = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Toggle code block state
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                continue
            
            if in_code_block:
                continue

            # Regex to find anything that looks like {Word}
            # matches `?\{([a-zA-Z0-9_]+)\}`?
            matches = re.finditer(r'`?\{([a-zA-Z0-9_]+)\}`?', line)
            for match in matches:
                kw = match.group(1)
                
                # Heuristic: Ignore very short keywords (likely variables like {x}, {i})
                if len(kw) <= 2:
                    continue
                    
                # Heuristic: Ignore common template variables
                if kw in ['name', 'value', 'key', 'item', 'result', 'count', 'index', 'file', 'content', 'config', 'msg', 'data', 'id', 'type', 'addr', 'src', 'dst']:
                    continue

                potential_keywords.append({
                    'keyword': kw,
                    'line': i + 1,
                    'context': stripped
                })
    except Exception as e:
        print(f"Error scanning {file_path}: {e}")
    return potential_keywords

def scan_file_for_links(file_path, root_dir):
    issues = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        file_mtime = os.path.getmtime(file_path)
        file_dir = os.path.dirname(file_path)
        
        in_code_block = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            # Markdown links: [text](path)
            # Regex to capture path. Ignore anchors (#...)
            # We assume standard markdown link syntax.
            matches = re.finditer(r'\[([^\]]+)\]\(([^)#\s]+)(?:#[^\s\)]*)?\)', line)
            for match in matches:
                link_text = match.group(1)
                link_target = match.group(2)
                
                # Ignore external links
                if link_target.startswith(('http://', 'https://', 'mailto:', 'ftp://')):
                    continue
                
                # Resolve path
                if link_target.startswith('/'):
                    # Treat / as root of workspace
                    target_abs = os.path.join(root_dir, link_target.lstrip('/\\'))
                else:
                    target_abs = os.path.join(file_dir, link_target)
                
                target_abs = os.path.normpath(target_abs)
                
                if not os.path.exists(target_abs):
                    # Try adding .md if missing (autolinking)
                    if not target_abs.endswith('.md') and os.path.exists(target_abs + '.md'):
                        target_abs += '.md'
                    else:
                        issues.append({
                            'type': 'Broken Link',
                            'keyword': link_target,
                            'line': i + 1,
                            'context': stripped,
                            'status': f"Path not found: {link_target}"
                        })
                        continue
                
                # Check for files only (ignore directories for timestamp check)
                if os.path.isfile(target_abs):
                    target_mtime = os.path.getmtime(target_abs)
                    # If referenced file is NEWER than referencing file, warn.
                    if target_mtime > file_mtime:
                        target_iso = datetime.fromtimestamp(target_mtime).strftime('%Y-%m-%d %H:%M')
                        file_iso = datetime.fromtimestamp(file_mtime).strftime('%Y-%m-%d %H:%M')
                        issues.append({
                            'type': 'Stale Reference?',
                            'keyword': link_target,
                            'line': i + 1,
                            'context': stripped,
                            'status': f"Referenced file is NEWER ({target_iso}) than this file ({file_iso}). Valid?"
                        })

    except Exception as e:
        pass
    return issues

def scan_file_for_decorated_words(file_path, valid_keywords):
    issues = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        in_code_block = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            # Check bold (**word**) and code (`word`)
            # If a strict match to a valid keyword is found inside these but NOT inside {}, report it.
            
            for kw in valid_keywords:
                # Regex for exact word match inside bold/code
                # We want to match `**Keyword**` or `` `Keyword` ``
                # But NOT `{Keyword}` (which is correct)
                
                bold_pattern = f"\\*\\*{kw}\\*\\*"
                code_pattern = f"`{kw}`"
                
                if re.search(bold_pattern, line) or re.search(code_pattern, line):
                     issues.append({
                        'type': 'Missing Syntax',
                        'keyword': kw,
                        'line': i + 1,
                        'context': stripped,
                        'status': f"Keyword found in bold/code but missing {{}}. Use `{{{kw}}}`."
                    })

    except Exception as e:
        pass
    return issues

def scan_paths(paths, root_dir, official_keywords):
    """Scan multiple paths for friction points. Returns structured results."""
    all_results = []
    total_friction_count = 0
    
    for path_str in paths:
        path = os.path.abspath(path_str)
        if not os.path.exists(path):
            continue
            
        if os.path.isfile(path):
            if not path.endswith('.md'): continue
            rel_path = os.path.relpath(path, root_dir).replace('\\', '/')
            issues = []
            issues.extend(scan_file_for_potential_keywords(path))
            # Filter unknowns
            issues = [i for i in issues if i['keyword'] not in official_keywords]
            # Link issues
            issues.extend(scan_file_for_links(path, root_dir))
            # Syntax issues
            issues.extend(scan_file_for_decorated_words(path, official_keywords))
            
            if issues:
                total_friction_count += len(issues)
                all_results.append({"file": rel_path, "issues": issues})
        else:
            # Recursive scan for directory
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d not in ['references', 'temp'] and not d.startswith('.')]
                for file in files:
                    if not file.endswith('.md'): continue
                    file_path = os.path.join(root, file)
                    results, count = scan_paths([file_path], root_dir, official_keywords)
                    all_results.extend(results)
                    total_friction_count += count
                    
    return all_results, total_friction_count

def main():
    parser = argparse.ArgumentParser(description="Friction Audit Tool")
    parser.add_argument("paths", nargs="*", help="Files or directories to audit")
    parser.add_argument("--requirements", help="Path to requirements list.md")
    parser.add_argument("--stdin-paths", "-p", action="store_true", help="Read paths from STDIN")
    parser.add_argument("--json", "-j", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    root_dir = os.getcwd()
    req_list_path = args.requirements or os.path.join(root_dir, 'docs', 'requires', 'requirement_list.md')
    
    if not os.path.exists(req_list_path):
        if not args.json: print(f"Requirements list not found at {req_list_path}")
        sys.exit(1)
    
    official_keywords = load_official_keywords(req_list_path)
    
    targets = []
    if args.stdin_paths:
        for line in sys.stdin:
            p = line.strip()
            if p: targets.append(p)
    
    if args.paths:
        targets.extend(args.paths)
            
    if not targets and not args.stdin_paths:
        # Default to docs directory if no paths provided
        targets = [os.path.join(root_dir, 'docs')]

    if not args.json:
        print(f"[*] Auditing friction points in {len(targets)} targets...")
    
    all_results, count = scan_paths(targets, root_dir, official_keywords)
    
    if args.json:
        import json
        print(json.dumps({"count": count, "results": all_results}, indent=2))
    else:
        report = "# Friction Audit Report\n\n"
        report += f"Audit conducted on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        for res in all_results:
            report += f"## {res['file']}\n"
            for i in res['issues']:
                st = i.get('status', i.get('type', 'Unknown'))
                report += f"- **Line {i['line']}**: `{{{i['keyword']}}}` -> {st}\n"
                report += f"  - Context: `{i['context']}`\n"
            report += "\n"

        output_path = os.path.join(root_dir, 'docs', 'temp', 'friction_report.md')
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"Audit complete. Found {count} friction points.")
        print(f"Report saved to {output_path}")

if __name__ == "__main__":
    main()
