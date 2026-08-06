#!/usr/bin/env python3
"""
Fireball Unified Document Verification Pipeline (Cross-Platform Python Runner)

Usage:
  uv run python tools/run_all.py [OPTIONS]
"""

import sys
import os
import json
import argparse
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

def run_command(cmd: list[str]) -> bool:
    try:
        res = subprocess.run(cmd, cwd=REPO_ROOT, check=True)
        return res.returncode == 0
    except Exception as e:
        print(f"Command failed: {' '.join(cmd)} - {e}")
        return False

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Fireball Unified Document Verification Pipeline")
    parser.add_argument("--llm", action="store_true", help="Run Graph-based LLM as a Judge semantic audits")
    parser.add_argument("--backend", default="auto", help="LLM backend (gemini, sakura, openrouter, ollama, mock)")
    parser.add_argument("--model", default="", help="LLM model name")
    parser.add_argument("--max-subgraphs", type=int, default=10, help="Max subgraphs to evaluate with LLM")

    args = parser.parse_args()

    print("================================================================================")
    print(" Fireball Unified Document Verification Pipeline (DocGraph Architecture)")
    print("================================================================================")
    if args.llm:
        print(f" Mode: Mechanical Graph Verification + LLM Subgraph Judge Audits")
        print(f" Backend: {args.backend}")
        print(f" Max Subgraphs: {args.max_subgraphs}")
    else:
        print(" Mode: Static Graph Verification Only (Use --llm to enable LLM Judge)")
    print("================================================================================")

    # Phase 1: Build DocGraph & Topology Check
    print("\n>>> [Phase 1/3] Building DocGraph and Verifying Graph Topology...")
    if not run_command([sys.executable, "tools/doc_graph.py", "docs", "--connected-only"]):
        print("✖ DocGraph Topology Check: FAILED")
        sys.exit(1)
    print("✔ DocGraph Construction & Topology Check: PASSED")

    # Phase 2: Extract Subgraphs
    print("\n>>> [Phase 2/3] Extracting Requirement-centric Evaluation Subgraphs...")
    temp_dir = REPO_ROOT / "temp"
    temp_dir.mkdir(exist_ok=True)
    subgraph_out = temp_dir / "subgraphs.json"
    
    if not run_command([sys.executable, "tools/doc_graph.py", "docs", "--subgraphs", "--out", str(subgraph_out)]):
        print("✖ Subgraph Extraction: FAILED")
        sys.exit(1)
    print(f"✔ Subgraph Extraction: PASSED (Saved to {subgraph_out})")

    # Phase 3: LLM Judge Audit
    if args.llm:
        print("\n>>> [Phase 3/3] Running Graph-based LLM as a Judge Audit...")
        judge_cmd = [
            sys.executable, "tools/doc_judge.py", "docs",
            "--backend", args.backend,
            "--max-subgraphs", str(args.max_subgraphs),
            "--out", str(temp_dir / "judge_report.json")
        ]
        if args.model:
            judge_cmd.extend(["--model", args.model])

        if not run_command(judge_cmd):
            print("✖ LLM Subgraph Judge Audit: FAILED")
            sys.exit(1)
        print("✔ LLM Subgraph Judge Audit: PASSED")
    else:
        print("\n>>> [Phase 3/3] Skipping LLM Subgraph Judge Audits (Use --llm to enable)")

    print("\n================================================================================")
    print(" Verification Pipeline Summary")
    print("================================================================================")
    print(" Result: SUCCESS (All enabled checks passed)")

if __name__ == "__main__":
    main()
