
import os
import re
import sys
import argparse

def load_keywords(req_list_path):
    keywords = set()
    try:
        with open(req_list_path, 'r', encoding='utf-8') as f:
            for line in f:
                # Extract keyword like `{Keyword}` from markdown table
                # Assuming format: | `{Keyword}` | ...
                match = re.search(r'`\{([a-zA-Z0-9_]+)\}`', line)
                if match:
                    keywords.add(match.group(1))
    except Exception as e:
        print(f"Error loading keywords from {req_list_path}: {e}")
        sys.exit(1)
    return keywords

def scan_file(file_path, keywords, context_lines=3):
    results = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for i, line in enumerate(lines):
            for kw in keywords:
                full_kw = f"{{{kw}}}"
                if full_kw in line:
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    context = "".join(lines[start:end])
                    results.append({
                        'keyword': kw,
                        'line_num': i + 1,
                        'context': context
                    })
    except Exception as e:
        print(f"Error scanning {file_path}: {e}")
    return results

def main():
    parser = argparse.ArgumentParser(description="Traceability Check Tool")
    parser.add_argument("paths", nargs="*", help="Files or directories to scan")
    parser.add_argument("--requirements", help="Path to requirements list.md")
    args = parser.parse_args()

    root_dir = os.getcwd()
    req_list_path = args.requirements or os.path.join(root_dir, 'docs', 'requires', 'requirement_list.md')
    
    if not os.path.exists(req_list_path):
        print(f"Requirements list not found at {req_list_path}")
        sys.exit(1)

    keywords = load_keywords(req_list_path)
    print(f"Loaded {len(keywords)} keywords.")

    targets = []
    
    import stat
    def has_piped_input():
        if sys.stdin.isatty(): return False
        try:
            mode = os.fstat(0).st_mode
            return stat.S_ISFIFO(mode) or stat.S_ISREG(mode) or stat.S_ISSOCK(mode)
        except:
            return False

    if has_piped_input():
        for line in sys.stdin:
            p = line.strip()
            if p: targets.append(p)
    if args.paths:
        targets.extend(args.paths)
    if not targets:
        # Default to docs directory
        targets = [os.path.join(root_dir, 'docs')]

    report = "# Traceability Report\n\n"
    found_count = 0
    missing_keywords = keywords.copy()

    def process_targets(targets_list):
        nonlocal found_count
        local_report = ""
        for target_str in targets_list:
            target = os.path.abspath(target_str)
            if not os.path.exists(target):
                continue
            if os.path.isfile(target):
                if not target.endswith('.md'): continue
                if os.path.abspath(target) == os.path.abspath(req_list_path): continue
                
                results = scan_file(target, keywords)
                if results:
                    local_report += f"## {os.path.relpath(target, root_dir)}\n\n"
                    for res in results:
                        found_count += 1
                        if res['keyword'] in missing_keywords:
                            missing_keywords.remove(res['keyword'])
                        local_report += f"### `{{{res['keyword']}}}` (Line {res['line_num']})\n"
                        local_report += "```markdown\n" + res['context'] + "```\n\n"
            else:
                for root, dirs, files in os.walk(target):
                    dirs[:] = [d for d in dirs if d not in ['references', 'temp'] and not d.startswith('.')]
                    for file in files:
                        local_report += process_targets([os.path.join(root, file)])
        return local_report

    report += process_targets(targets)

    report += "## Missing Keywords\n"
    for kw in sorted(missing_keywords):
        report += f"- `{{{kw}}}`\n"

    output_path = os.path.join(root_dir, 'docs', 'temp', 'traceability_report.md')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"Scan complete. Found {found_count} occurrences.")
    print(f"Report saved to {output_path}")

if __name__ == "__main__":
    main()
