"""
Mermaid diagram syntax validator for documentation.
Checks Mermaid diagrams embedded in markdown files for common syntax errors.
Returns violations in the same format as other mechanical checks.
"""

import re
from pathlib import Path
from typing import List, Dict, Tuple


def check_mermaid(component_files: List[Path]) -> List[Dict]:
    """
    Validate Mermaid diagram syntax in markdown files.

    Args:
        component_files: List of markdown files to validate

    Returns:
        List of violation dicts with keys: rule_code, file_path, line_number, message
    """
    violations = []

    for filepath in component_files:
        content = filepath.read_text(encoding='utf-8')
        diagrams = _extract_diagrams(content)

        for diagram_type, diagram_text, line_num in diagrams:
            errors = _validate_diagram(diagram_type, diagram_text, line_num)
            for error_msg in errors:
                violations.append({
                    'rule_code': 'M-MERMAID-SYNTAX',
                    'file_path': filepath,
                    'line_number': line_num,
                    'message': error_msg
                })

    return violations


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


def _validate_diagram(diagram_type: str, diagram_text: str, line_num: int) -> List[str]:
    """Validate a single diagram and return error messages."""
    errors = []

    if diagram_type == 'state':
        errors.extend(_validate_state_diagram(diagram_text))
    elif diagram_type == 'sequence':
        errors.extend(_validate_sequence_diagram(diagram_text))
    elif diagram_type == 'graph':
        errors.extend(_validate_graph_diagram(diagram_text))

    return errors


def _validate_state_diagram(text: str) -> List[str]:
    """Validate state diagram syntax."""
    errors = []

    # Check for unmatched braces in composite states
    open_braces = text.count('{')
    close_braces = text.count('}')
    if open_braces != close_braces:
        errors.append(
            f'State diagram: Unmatched braces ({{ {open_braces}, }} {close_braces})'
        )

    # Check for state definitions with [*]
    lines = text.split('\n')
    for line in lines:
        if '-->' in line:
            parts = line.split('-->')
            if len(parts) != 2:
                errors.append(f'Invalid state transition: "{line.strip()}"')

    return errors


def _validate_sequence_diagram(text: str) -> List[str]:
    """Validate sequence diagram syntax."""
    errors = []

    # Check for balanced activate/deactivate using a stack
    lines = text.split('\n')
    activate_stack = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('activate '):
            match = re.search(r'activate\s+(\w+)', stripped)
            if match:
                activate_stack.append(match.group(1))
        elif stripped.startswith('deactivate '):
            match = re.search(r'deactivate\s+(\w+)', stripped)
            if match:
                participant = match.group(1)
                if not activate_stack or activate_stack[-1] != participant:
                    errors.append(
                        f'Sequence diagram: Mismatched deactivate "{participant}" at line {i + 1}'
                    )
                else:
                    activate_stack.pop()

    if activate_stack:
        errors.append(
            f'Sequence diagram: Unmatched activate for: {", ".join(activate_stack)}'
        )

    return errors


def _validate_graph_diagram(text: str) -> List[str]:
    """Validate graph/flowchart diagram syntax."""
    errors = []

    # Check for unmatched brackets/parentheses
    open_square = text.count('[')
    close_square = text.count(']')
    open_round = text.count('(')
    close_round = text.count(')')

    if open_square != close_square:
        errors.append(
            f'Graph diagram: Unmatched square brackets ([ {open_square}, ] {close_square})'
        )

    if open_round != close_round:
        errors.append(
            f'Graph diagram: Unmatched parentheses (( {open_round}, ) {close_round})'
        )

    return errors
