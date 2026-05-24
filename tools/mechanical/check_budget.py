#!/usr/bin/env python3
"""
Budget Verification Tool for Fireball

Validates that system components stay within their allocated RAM, ROM, and SLOC budgets.
Reads constraint definitions from docs/architecture/resource_budget.md and compares against actual metrics.

Usage:
  python3 tools/mechanical/check_budget.py [--verbose]
"""

import sys
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class BudgetViolation:
    """Represents a budget constraint violation."""

    def __init__(self, rule_code: str, file_path: str, component: str,
                 metric: str, actual: float, budget: float):
        self.rule_code = rule_code
        self.file_path = file_path
        self.component = component
        self.metric = metric
        self.actual = actual
        self.budget = budget
        self.exceeded_pct = ((actual - budget) / budget * 100) if budget > 0 else 0


def parse_budget_constraints(budget_file: Path) -> Dict[str, Dict[str, float]]:
    """
    Parse RAM/ROM/SLOC budget constraints from resource_budget.md

    Returns:
        Dictionary with structure:
        {
            'constraints': {
                'SystemMemoryLimit': 64,  # KB
                'CodeSizeLimit': 15000,   # SLOC
                'JITCacheLimit': 4,       # KB
                ...
            },
            'components': {
                'COOS Kernel': {'RAM': 4.0, 'ROM': 16, 'SLOC': 4000},
                'vSoC Runtime': {'RAM': 2.0, 'ROM': 32, 'SLOC': 6000},
                ...
            }
        }
    """
    content = budget_file.read_text(encoding='utf-8')

    result = {
        'constraints': {},
        'components': {},
        'totals': {}
    }

    # Parse constraint blocks (section 4.1)
    constraint_section = re.search(
        r'### 4\.1 制約ブロック定義(.*?)(?=### 4\.1\.1|### 4\.2|## 5)',
        content, re.DOTALL
    )
    if constraint_section:
        # Extract constraint table
        table_match = re.search(
            r'\| \*\*SystemMemoryLimit\*\*.*?\| (≤ \d+)[^|]*\|',
            constraint_section.group(1)
        )
        if table_match:
            result['constraints']['SystemMemoryLimit'] = 64  # KB

    # Parse component budgets (section 4.2)
    components_section = re.search(
        r'### 4\.2 コンポーネント予算配分\s*\n\n(.+?)\n\n###',
        content, re.DOTALL
    )
    if components_section:
        section_text = components_section.group(1)
        lines = section_text.split('\n')
        header_seen = False

        for line in lines:
            if not line.strip() or '|' not in line:
                continue

            # Skip header and separator lines
            if 'コンポーネント' in line or '---' in line:
                header_seen = True
                continue

            if not header_seen:
                continue

            # Parse table row
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 6:
                continue

            component = parts[1].replace('**', '').strip()

            # Skip total row
            if component in ['合計', '']:
                continue

            try:
                # Extract numbers from KB/行 notation
                ram_str = parts[2]
                rom_str = parts[3]
                sloc_str = parts[4]

                ram = float(re.search(r'[\d.]+', ram_str).group()) if ram_str and '-' not in ram_str else 0
                rom = float(re.search(r'[\d.]+', rom_str).group()) if rom_str and '-' not in rom_str else 0
                sloc_match = re.search(r'[\d,]+', sloc_str)
                sloc = int(sloc_match.group().replace(',', '')) if sloc_match and '-' not in sloc_str else 0

                if component and (ram > 0 or rom > 0 or sloc > 0):
                    result['components'][component] = {
                        'RAM': ram,
                        'ROM': rom,
                        'SLOC': sloc
                    }
            except (ValueError, IndexError, AttributeError):
                pass

    # Extract total budgets
    total_match = re.search(
        r'\| \*\*合計\*\*\s*\|\s*\*\*(\d+(?:\.\d+)?)\s*KB\*\*.*?' +
        r'\|\s*\*\*(\d+(?:\.\d+)?)\s*KB\*\*.*?' +
        r'\|\s*\*\*~?(\d+(?:,\d+)?)\s*行\*\*',
        content
    )
    if total_match:
        result['totals'] = {
            'RAM': float(total_match.group(1)),
            'ROM': float(total_match.group(2)),
            'SLOC': int(total_match.group(3).replace(',', ''))
        }

    return result


def get_component_metrics() -> Dict[str, Dict[str, float]]:
    """
    Get actual metrics for each component.

    This is a placeholder that would integrate with:
    - Linker script analysis for actual RAM/ROM usage
    - SLOC counting via cloc
    - Runtime profiling for JIT cache usage
    """
    # For now, return Phase 0.8 planned metrics (from backlog)
    return {
        'COOS Kernel': {'RAM': 3.5, 'ROM': 16, 'SLOC': 4000},
        'vSoC Runtime': {'RAM': 1.8, 'ROM': 32, 'SLOC': 6000},
        'IPC Router': {'RAM': 1.5, 'ROM': 8, 'SLOC': 2000},
        'HAL / Drivers': {'RAM': 1.5, 'ROM': 16, 'SLOC': 1500},
        'Logging': {'RAM': 1.0, 'ROM': 4, 'SLOC': 500},
        'JIT Code Cache': {'RAM': 4.0, 'ROM': 0, 'SLOC': 0},
        'WASM Linear Memory': {'RAM': 4.5, 'ROM': 32, 'SLOC': 1500},
        'Metadata / Config': {'RAM': 2.0, 'ROM': 20, 'SLOC': 0},
        # Safety Margin intentionally excluded (not a component)
    }


def verify_budgets(budget_file: Path, verbose: bool = False) -> List[BudgetViolation]:
    """
    Verify that actual metrics stay within budgeted allocations.

    Args:
        budget_file: Path to resource_budget.md
        verbose: Print verbose output

    Returns:
        List of BudgetViolation objects for any exceeded constraints
    """
    violations = []

    # Parse constraints and budget allocations
    budgets = parse_budget_constraints(budget_file)

    if verbose:
        print("Parsed Budgets:")
        print(f"  Constraints: {budgets['constraints']}")
        print(f"  Components: {len(budgets['components'])} defined")
        print(f"  Totals: {budgets['totals']}")

    # Get actual metrics
    actual = get_component_metrics()

    # Check individual component budgets
    for component, budget in budgets['components'].items():
        if component in actual:
            actual_metrics = actual[component]

            # Check RAM
            if actual_metrics['RAM'] > budget['RAM']:
                violations.append(BudgetViolation(
                    'M-BUDGET-RAM-EXCEED',
                    str(budget_file),
                    component,
                    'RAM (KB)',
                    actual_metrics['RAM'],
                    budget['RAM']
                ))

            # Check ROM
            if actual_metrics['ROM'] > budget['ROM']:
                violations.append(BudgetViolation(
                    'M-BUDGET-ROM-EXCEED',
                    str(budget_file),
                    component,
                    'ROM (KB)',
                    actual_metrics['ROM'],
                    budget['ROM']
                ))

            # Check SLOC
            if actual_metrics['SLOC'] > 0 and budget['SLOC'] > 0:
                if actual_metrics['SLOC'] > budget['SLOC']:
                    violations.append(BudgetViolation(
                        'M-BUDGET-SLOC-EXCEED',
                        str(budget_file),
                        component,
                        'SLOC',
                        actual_metrics['SLOC'],
                        budget['SLOC']
                    ))

    # Check total budgets
    total_ram = sum(m.get('RAM', 0) for m in actual.values())
    total_rom = sum(m.get('ROM', 0) for m in actual.values())
    total_sloc = sum(m.get('SLOC', 0) for m in actual.values())

    if 'RAM' in budgets['totals']:
        if total_ram > budgets['totals']['RAM']:
            violations.append(BudgetViolation(
                'M-BUDGET-TOTAL-RAM',
                str(budget_file),
                'System Total',
                'RAM (KB)',
                total_ram,
                budgets['totals']['RAM']
            ))

    if 'ROM' in budgets['totals']:
        if total_rom > budgets['totals']['ROM']:
            violations.append(BudgetViolation(
                'M-BUDGET-TOTAL-ROM',
                str(budget_file),
                'System Total',
                'ROM (KB)',
                total_rom,
                budgets['totals']['ROM']
            ))

    if 'SLOC' in budgets['totals']:
        if total_sloc > budgets['totals']['SLOC']:
            violations.append(BudgetViolation(
                'M-BUDGET-TOTAL-SLOC',
                str(budget_file),
                'System Total',
                'SLOC',
                total_sloc,
                budgets['totals']['SLOC']
            ))

    return violations


def check_budget(budget_file: Optional[Path] = None, verbose: bool = False) -> List[Dict]:
    """
    Check resource budgets and return violations in standard format.

    Args:
        budget_file: Path to resource_budget.md (defaults to docs/architecture/resource_budget.md)
        verbose: Print verbose output

    Returns:
        List of violation dictionaries with keys:
        - rule_code: Violation code (M-BUDGET-*)
        - file_path: Path to budget file
        - line_number: Line number (0 for totals)
        - message: Human-readable description
    """
    if budget_file is None:
        budget_file = Path(__file__).parent.parent.parent / 'docs' / 'architecture' / 'resource_budget.md'

    if not budget_file.exists():
        return [{
            'rule_code': 'M-BUDGET-FILE-NOT-FOUND',
            'file_path': str(budget_file),
            'line_number': 0,
            'message': f'Budget file not found: {budget_file}'
        }]

    violations = verify_budgets(budget_file, verbose=verbose)

    results = []
    for v in violations:
        results.append({
            'rule_code': v.rule_code,
            'file_path': v.file_path,
            'line_number': 0,  # Would need to parse file to find exact line
            'message': f'{v.component}: {v.metric} {v.actual:.1f} exceeds budget {v.budget:.1f} ' +
                      f'(+{v.exceeded_pct:.1f}%)'
        })

    if verbose:
        print(f"\nBudget check: {len(results)} violation(s) found")
        for r in results:
            print(f"  [{r['rule_code']}] {r['message']}")

    return results


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Verify Fireball resource budgets')
    parser.add_argument('--budget-file', type=Path, help='Path to resource_budget.md')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    results = check_budget(budget_file=args.budget_file, verbose=args.verbose)

    for r in results:
        print(f"[{r['rule_code']}] {r['file_path']}: {r['message']}")

    sys.exit(0 if not results else 1)
