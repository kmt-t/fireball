"""
Mermaid diagram syntax validator for documentation.
Checks Mermaid diagrams embedded in markdown files for common syntax errors.
Returns violations in the same format as other mechanical checks.

Validation rules are defined in mermaid_config.csv to avoid hard-coding.
"""

import csv
import re
from pathlib import Path
from typing import List, Dict, Tuple, Set


def check_mermaid(component_files: List[Path]) -> List[Dict]:
    """
    Validate Mermaid diagram syntax in markdown files.

    Args:
        component_files: List of markdown files to validate

    Returns:
        List of violation dicts with keys: rule_code, file_path, line_number, message
    """
    violations = []
    rules = _load_rules()

    for filepath in component_files:
        content = filepath.read_text(encoding='utf-8')
        diagrams = _extract_diagrams(content)

        for diagram_type, diagram_text, line_num in diagrams:
            errors = _validate_diagram(diagram_type, diagram_text, line_num, rules)
            for rule_code, error_msg in errors:
                violations.append({
                    'rule_code': rule_code,
                    'file_path': filepath,
                    'line_number': line_num,
                    'message': error_msg
                })

    return violations


def _load_rules() -> Dict[str, List[Dict]]:
    """Load validation rules from mermaid_config.csv."""
    rules = {}
    config_path = Path(__file__).parent.parent / 'config' / 'mermaid_config.csv'

    if not config_path.exists():
        return rules

    with open(config_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            diagram_type = row['diagram_type']
            if diagram_type not in rules:
                rules[diagram_type] = []
            rules[diagram_type].append({
                'rule_name': row['rule_name'],
                'rule_code': row['rule_code'],
                'description': row['description']
            })

    return rules


def _extract_diagrams(content: str) -> List[Tuple[str, str, int]]:
    """Extract all Mermaid diagrams from markdown content."""
    diagrams = []
    pattern = r'```mermaid\n(.*?)\n```'

    for match in re.finditer(pattern, content, re.DOTALL):
        diagram_text = match.group(1)
        line_num = content[:match.start()].count('\n') + 1
        first_line = diagram_text.split('\n')[0].strip()
        diagram_type = _detect_type(first_line)
        diagrams.append((diagram_type, diagram_text, line_num))

    return diagrams


def _detect_type(first_line: str) -> str:
    """Detect diagram type from first line."""
    if 'stateDiagram' in first_line:
        return 'state'
    elif 'sequenceDiagram' in first_line:
        return 'sequence'
    elif 'graph' in first_line:
        return 'graph'
    else:
        return 'unknown'


def _validate_diagram(diagram_type: str, diagram_text: str, line_num: int,
                      rules: Dict[str, List[Dict]]) -> List[Tuple[str, str]]:
    """
    Validate a single diagram using rules from configuration.

    Returns:
        List of (rule_code, error_message) tuples
    """
    errors = []

    if diagram_type not in rules:
        return errors

    for rule in rules[diagram_type]:
        rule_name = rule['rule_name']
        rule_code = rule['rule_code']

        # Map rule names to validation functions
        if rule_name == 'brace_balance':
            msg = _check_brace_balance(diagram_text)
            if msg:
                errors.append((rule_code, msg))
        elif rule_name == 'transition_syntax':
            msg = _check_transition_syntax(diagram_text)
            if msg:
                errors.append((rule_code, msg))
        elif rule_name == 'activate_balance':
            msg = _check_activate_balance(diagram_text)
            if msg:
                errors.append((rule_code, msg))
        elif rule_name == 'bracket_balance':
            msg = _check_bracket_balance(diagram_text)
            if msg:
                errors.append((rule_code, msg))
        elif rule_name == 'parenthesis_balance':
            msg = _check_parenthesis_balance(diagram_text)
            if msg:
                errors.append((rule_code, msg))

    return errors


def _check_brace_balance(text: str) -> str:
    """Check for balanced braces in composite states. Returns error message or empty string."""
    open_braces = text.count('{')
    close_braces = text.count('}')
    if open_braces != close_braces:
        return f'Unmatched braces ({{ {open_braces}, }} {close_braces})'
    return ''


def _check_transition_syntax(text: str) -> str:
    """Check state transition syntax. Returns error message or empty string."""
    lines = text.split('\n')
    for i, line in enumerate(lines, 1):
        if '-->' in line:
            # Basic check: ensure proper spacing around arrow
            if line.count('-->') != 1:
                return f'Multiple transitions on line {i}: "{line.strip()}"'
    return ''


def _check_activate_balance(text: str) -> str:
    """Check balanced activate/deactivate. Returns error message or empty string."""
    lines = text.split('\n')
    activate_stack = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('activate '):
            match = re.search(r'activate\s+(\w+)', stripped)
            if match:
                activate_stack.append((match.group(1), i))
        elif stripped.startswith('deactivate '):
            match = re.search(r'deactivate\s+(\w+)', stripped)
            if match:
                participant = match.group(1)
                if not activate_stack or activate_stack[-1][0] != participant:
                    return f'Mismatched deactivate "{participant}" at line {i}'
                activate_stack.pop()

    if activate_stack:
        unmatched = ', '.join(f'{name} (line {line_num})'
                               for name, line_num in activate_stack)
        return f'Unmatched activate: {unmatched}'
    return ''


def _check_bracket_balance(text: str) -> str:
    """Check balanced square brackets. Returns error message or empty string."""
    open_square = text.count('[')
    close_square = text.count(']')
    if open_square != close_square:
        return f'Unmatched square brackets ([ {open_square}, ] {close_square})'
    return ''


def _check_parenthesis_balance(text: str) -> str:
    """Check balanced parentheses. Returns error message or empty string."""
    open_round = text.count('(')
    close_round = text.count(')')
    if open_round != close_round:
        return f'Unmatched parentheses (( {open_round}, ) {close_round})'
    return ''
