
import os
import re
import sys

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
    root_dir = os.getcwd()
    docs_dir = os.path.join(root_dir, 'docs')
    req_list_path = os.path.join(docs_dir, 'requires', 'list.md')
    
    if len(sys.argv) > 1:
        req_list_path = sys.argv[1]

    if not os.path.exists(req_list_path):
        print(f"Requirements list not found at {req_list_path}")
        sys.exit(1)

    keywords = load_keywords(req_list_path)
    print(f"Loaded {len(keywords)} keywords.")

    report = "# Traceability Report\n\n"
    
    found_count = 0
    missing_keywords = keywords.copy()

    for root, dirs, files in os.walk(docs_dir):
        for file in files:
            if not file.endswith('.md'):
                continue
            
            file_path = os.path.join(root, file)
            # Skip the requirements list itself to avoid self-reference noise
            if os.path.abspath(file_path) == os.path.abspath(req_list_path):
                continue
                
            results = scan_file(file_path, keywords)
            if results:
                report += f"## {os.path.relpath(file_path, root_dir)}\n\n"
                for res in results:
                    found_count += 1
                    if res['keyword'] in missing_keywords:
                        missing_keywords.remove(res['keyword'])
                    
                    report += f"### `{{{res['keyword']}}}` (Line {res['line_num']})\n"
                    report += "```markdown\n"
                    report += res['context']
                    report += "```\n\n"

    report += "## Missing Keywords\n"
    report += "The following keywords were not found in any other document:\n\n"
    for kw in sorted(missing_keywords):
        report += f"- `{{{kw}}}`\n"

    output_path = os.path.join(docs_dir, 'temp', 'traceability_report.md')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"Scan complete. Found {found_count} occurrences.")
    print(f"Report saved to {output_path}")

if __name__ == "__main__":
    main()
