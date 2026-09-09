#!/usr/bin/env python3
"""
collect_arch_context.py

Parses docs/architecture/architecture_overview.md, extracts metadata keywords,
cross-references with keyword_dictionary.md, and outputs relevant specification,
formal model, WIT, and concept code files grouped by audit domain.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _SKILL_DIR.parent.parent


def find_repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        if (cur / "docs").is_dir() and (cur / "AGENTS.md").is_file():
            return cur
        cur = cur.parent
    return _REPO_ROOT


# Domain mapping for architecture overview review
DOMAINS = {
    "abi": {
        "name": "ABI, Context & Memory Layout",
        "description": "execution_context layout, registers, CPS calling convention, frames",
        "keywords": [
            "ContextPointerRegister",
            "EnvironmentPointer",
            "JIT_RegisterMapping",
            "ADR_TosCacheAsymmetry",
            "AAPCS_FastCall",
            "ExecutionContext_Layout",
            "CallFrame_Layout",
            "ControlFrame_Layout",
            "VsocRuntime_Layout",
            "JITC-GOTCHA-01",
            "JITC-GOTCHA-02",
        ],
        "primary_docs": [
            "docs/architecture/architecture_overview.md",
            "docs/components/tier2_runtime/runtime_interpreter.md",
            "docs/components/tier2_runtime/runtime_vsoc.md",
            "docs/components/tier3_jit/jit_compiler.md",
            "docs/specs/jit_stencil_catalog.md",
        ],
        "wit_files": [
            "docs/components/tier2_runtime/wit/vsoc_runtime.wit",
        ],
        "concept_files": [
            "docs/components/tier3_jit/concepts/jit_copy_patch_concept.py",
        ],
    },
    "jit": {
        "name": "JIT Pipeline, Cache & Dispatch",
        "description": "3-Bank Generational Cache, Radix Table dispatch, trace chaining, LIFO compile",
        "keywords": [
            "RadixTable_Dispatch",
            "FlatViewNarrowing",
            "META_BinarySearch",
            "JIT_MultiBuffer_Cache",
            "JIT_OldestOnly_Promote",
            "SimpleJITArchitecture",
            "JITR-GOTCHA-02",
            "JITR-GOTCHA-03",
        ],
        "primary_docs": [
            "docs/architecture/architecture_overview.md",
            "docs/components/tier3_jit/jit_runtime.md",
            "docs/components/tier3_jit/jit_compiler.md",
            "docs/components/tier2_runtime/runtime_vsoc.md",
        ],
        "formal_models": [
            "docs/components/tier2_runtime/formal/vsoc_cache_coherency_model.py",
            "docs/components/tier3_jit/formal/jit_cache_model.py",
        ],
        "concept_files": [
            "docs/components/tier3_jit/concepts/jit_runtime_concept.py",
        ],
    },
    "ipc_mem": {
        "name": "CSP Communication, Shared Memory & Memory Safety",
        "description": "Symmetric direct handoff, bufferless rendezvous, move-only SharedBlock, folding XOR TLB, unmap security",
        "keywords": [
            "ADR_RendezvousChannel",
            "CSP_Handoff",
            "DirectContextSwitch",
            "FastAddressCheck",
            "META_RestrictedPhysicalAccess",
            "LowLatencyLookup",
            "IPC_ZeroCopy",
            "TypeSafeMessaging",
            "ADR_SharedBlockRaii",
            "COOS-GOTCHA-01",
            "COOS-GOTCHA-02",
            "MEM-GOTCHA-03",
        ],
        "primary_docs": [
            "docs/architecture/architecture_overview.md",
            "docs/components/tier1_core/os_coos.md",
            "docs/components/tier1_interface/ipc_router.md",
            "docs/components/tier2_runtime/runtime_vmmio.md",
            "docs/components/tier1_core/system_memory.md",
            "docs/components/tier2_runtime/runtime_memory.md",
        ],
        "formal_models": [
            "docs/components/tier1_core/formal/coos_channel_model.py",
            "docs/components/tier1_interface/formal/csp_handoff_model.py",
        ],
        "concept_files": [
            "docs/components/tier1_core/concepts/coos_concept.py",
            "docs/components/tier1_interface/concepts/ipc_router_concept.py",
            "docs/components/tier2_runtime/concepts/vmmio_concept.py",
            "docs/components/tier2_runtime/concepts/runtime_memory_concept.py",
        ],
    },
    "traceability": {
        "name": "Requirements, WIT & Keyword Traceability",
        "description": "Requirement list consistency, WIT specifications, keyword dictionary anchors",
        "keywords": [
            "GLOBAL_ComponentHarness",
            "META_StaticDI",
            "META_ZeroOverhead",
            "UnifiedAccessModel",
            "Challenge_SyscallMemorySafety",
        ],
        "primary_docs": [
            "docs/architecture/architecture_overview.md",
            "docs/architecture/keyword_dictionary.md",
            "docs/architecture/document_structure.md",
            "docs/requires/requirement_list.md",
        ],
        "wit_files": [
            "docs/components/tier1_interface/wit/ipc_subsystem.wit",
            "docs/components/tier2_runtime/wit/vsoc_runtime.wit",
        ],
    },
}


def extract_keywords(file_path: Path) -> list[str]:
    if not file_path.is_file():
        return []
    content = file_path.read_text(encoding="utf-8")
    return sorted(list(set(re.findall(r"\{([A-Za-z0-9_]+)\}", content))))


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect architectural review context files.")
    parser.add_argument(
        "--domain",
        choices=["abi", "jit", "ipc_mem", "traceability", "all"],
        default="all",
        help="Audit domain to filter context (default: all)",
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    repo_root = find_repo_root()
    arch_doc = repo_root / "docs" / "architecture" / "architecture_overview.md"

    if not arch_doc.is_file():
        print(f"Error: {arch_doc} not found", file=sys.stderr)
        return 1

    extracted_kw = extract_keywords(arch_doc)

    selected_domains = DOMAINS.keys() if args.domain == "all" else [args.domain]

    result: dict[str, object] = {
        "architecture_doc": str(arch_doc.relative_to(repo_root)).replace("\\", "/"),
        "total_keywords_in_arch": len(extracted_kw),
        "domains": {},
    }

    for d_key in selected_domains:
        d_info = DOMAINS[d_key]
        domain_entry: dict[str, object] = {
            "name": d_info["name"],
            "description": d_info["description"],
            "tracked_keywords": d_info["keywords"],
            "files": {},
        }

        for category in ["primary_docs", "wit_files", "formal_models", "concept_files"]:
            file_list = d_info.get(category, [])
            resolved_files = []
            for f_rel in file_list:
                f_path = repo_root / f_rel
                resolved_files.append(
                    {
                        "path": str(f_path).replace("\\", "/"),
                        "exists": f_path.is_file(),
                    }
                )
            if resolved_files:
                domain_entry["files"][category] = resolved_files

        result["domains"][d_key] = domain_entry

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"=== Architecture Context Overview for '{arch_doc.name}' ===")
        print(f"Total Keywords: {len(extracted_kw)}")
        for d_key, d_data in result["domains"].items():
            print(f"\n--- Domain: {d_data['name']} ({d_key}) ---")
            print(f"Description: {d_data['description']}")
            print(f"Tracked Keywords: {', '.join(d_data['tracked_keywords'])}")
            for cat, files in d_data["files"].items():
                print(f"  {cat}:")
                for f in files:
                    status = "EXISTS" if f["exists"] else "MISSING"
                    print(f"    - [{status}] {f['path']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
