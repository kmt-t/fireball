#!/usr/bin/env python3
"""
.agents/skills/risk-assessor/scripts/assess_risks.py
Fireball システムのリスクテーマを解析・評価し、検証優先度と必要な検証部品を出力する
"""

import sys
import json
from pathlib import Path

# Add project root to sys.path
SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verifier.risk_extractor import scan_risk_themes


def evaluate_risk_level(theme: dict) -> str:
    kw = theme.get("keyword", "")
    desc = theme.get("description", "")
    
    if "InterruptSafety" in kw or "SyscallMemorySafety" in kw or "CspHandoffStarvation" in kw or "JIT" in kw:
        return "HIGH"
    elif "FD" in kw or "BlockedList" in kw or "Yield" in kw:
        return "MEDIUM"
    return "LOW"


def get_required_verification_component(theme: dict) -> str:
    kw = theme.get("keyword", "")
    if "InterruptSafety" in kw:
        return "InterruptSafetyVerifier"
    elif "CspHandoffStarvation" in kw:
        return "CspHandoffVerifier"
    elif "FD" in kw:
        return "VirtualFdTableVerifier"
    elif "SyscallMemorySafety" in kw:
        return "SyscallMemorySafetyVerifier"
    elif "JIT" in kw:
        return "JITCacheDoubleBufferVerifier"
    return "GenericStateVerifier"


def main():
    themes = scan_risk_themes()
    assessed_list = []

    print("=== Fireball リスク評価 & 必要検証部品マッピング ===")
    for t in themes:
        risk_level = evaluate_risk_level(t)
        verifier_comp = get_required_verification_component(t)
        entry = {
            "id": t["id"],
            "keyword": t["keyword"],
            "title": t["title"],
            "risk_level": risk_level,
            "required_verifier": verifier_comp,
            "description": t["description"]
        }
        assessed_list.append(entry)
        print(f"[{risk_level}] {t['keyword']} ({t['title']}) -> 必要部品: {verifier_comp}")

    out_file = REPO_ROOT / "tools" / "config" / "assessed_verification_risks.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(assessed_list, f, ensure_ascii=False, indent=2)

    print(f"\n評価完了: {len(assessed_list)} 件のテーマを評価 -> {out_file}")


if __name__ == "__main__":
    main()
