#!/usr/bin/env python3
"""
tools/verifier/components/run_all_components.py
Fireball リスク評価に基づいてピックアップされた全形式検証部品を一括実行するランナー
"""

import sys
from pathlib import Path

# Add project root to sys.path
SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verifier.components.interrupt_safety import verify_interrupt_safety
from tools.verifier.components.csp_handoff import verify_csp_handoff
from tools.verifier.components.syscall_memory_safety import verify_syscall_memory_safety
from tools.verifier.components.jit_cache_safety import verify_jit_cache_safety


def run_all():
    print("=== Fireball リスクベース検証部品 一括実行 ===")
    verifiers = [
        ("InterruptSafety", verify_interrupt_safety),
        ("CspHandoff", verify_csp_handoff),
        ("SyscallMemorySafety", verify_syscall_memory_safety),
        ("JITCacheDoubleBuffer", verify_jit_cache_safety),
    ]

    all_passed = True
    for name, v_func in verifiers:
        result = v_func()
        status = "PASS" if result.is_valid else "FAIL"
        if not result.is_valid:
            all_passed = False
        print(f"[{status}] {name} ({result.property_name}) - 探索状態数: {result.checked_states_count}")
        print(f"       Traceability: {', '.join(result.keywords)}")

    print(f"\n最終検証結果: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
