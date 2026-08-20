#!/usr/bin/env python3
"""
tools/verifier/evaluate_logs.py
検証ログおよびレポート評価サマリ生成ツール
"""
import os
import sys
import re
import json
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
for candidate in SCRIPT_PATH.parents:
    if (candidate / "verify").is_dir() and (candidate / "tools").is_dir():
        REPO_ROOT = candidate
        break
else:
    REPO_ROOT = Path.cwd()

def evaluate_reports():
    reports_dir = REPO_ROOT / "verify" / "reports"
    results = []
    
    if reports_dir.exists():
        for report_file in reports_dir.glob("*.md"):
            content = report_file.read_text(encoding="utf-8")
            
            # Check for verification status
            is_passed = "PASSED" in content or "成功" in content or "No error" in content
            counterexample = "Counterexample" in content or "反例" in content
            
            results.append({
                "report_name": report_file.name,
                "passed": is_passed and not counterexample,
                "has_counterexample": counterexample,
                "path": str(report_file.relative_to(REPO_ROOT)).replace("\\", "/")
            })

    return results

def main():
    results = evaluate_reports()
    summary_path = REPO_ROOT / "verify" / "reports" / "VERIFICATION_SUMMARY.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print(f"Evaluated {len(results)} verification reports -> {summary_path}")

if __name__ == "__main__":
    main()
