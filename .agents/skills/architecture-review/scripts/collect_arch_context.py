#!/usr/bin/env python3
"""architecture_overview.md の Tier・責務・依存関係監査用コンテキストを収集する。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypedDict


REPO_ROOT = Path(__file__).resolve().parents[4]
ARCHITECTURE_PATH = REPO_ROOT / "docs" / "architecture" / "architecture_overview.md"


class TierDefinition(TypedDict):
    label: str
    directory: str


class ComponentEntry(TypedDict):
    name: str
    path: str
    exists: bool


class WitEntry(TypedDict):
    name: str
    path: str
    exists: bool


class TierContext(TypedDict):
    label: str
    directory: str
    components: list[ComponentEntry]
    wit_contracts: list[WitEntry]


class ArchitectureContext(TypedDict):
    architecture_document: str
    tiers: dict[str, TierContext]
    graph_nodes: list[str]
    wit_contracts: dict[str, list[WitEntry]]
    ambiguous_wit_names: list[str]
    component_links: list[str]
    missing_links: list[str]
    unlinked_components: list[str]
    unknown_links: list[str]


TIERS: dict[str, TierDefinition] = {
    "tier1_core": {"label": "Tier 1 Core", "directory": "docs/components/tier1_core"},
    "tier1_interface": {
        "label": "Tier 1 Interface",
        "directory": "docs/components/tier1_interface",
    },
    "tier2_runtime": {
        "label": "Tier 2 Runtime",
        "directory": "docs/components/tier2_runtime",
    },
    "tier3_executer": {
        "label": "Tier 3 Executer",
        "directory": "docs/components/tier3_executer",
    },
    "tier3_plugins": {
        "label": "Tier 3 Plugins",
        "directory": "docs/components/tier3_plugins",
    },
    "tier3_platform": {
        "label": "Tier 3 Platform",
        "directory": "docs/components/tier3_platform",
    },
}


def read_architecture() -> str:
    return ARCHITECTURE_PATH.read_text(encoding="utf-8")


def collect_components(tier_name: str) -> list[ComponentEntry]:
    tier = TIERS[tier_name]
    directory = REPO_ROOT / tier["directory"]
    documents = sorted(path for path in directory.glob("*.md") if path.is_file())
    return [
        {
            "name": document.stem,
            "path": document.relative_to(REPO_ROOT).as_posix(),
            "exists": True,
        }
        for document in documents
    ]


def collect_wit_contracts(tier_name: str) -> list[WitEntry]:
    tier = TIERS[tier_name]
    directory = REPO_ROOT / tier["directory"] / "wit"
    documents = sorted(path for path in directory.glob("*.wit") if path.is_file())
    return [
        {
            "name": document.stem,
            "path": document.relative_to(REPO_ROOT).as_posix(),
            "exists": True,
        }
        for document in documents
    ]


def extract_component_links(text: str) -> list[str]:
    links: set[str] = set()
    for token in text.split("]("):
        if not token.startswith("docs/components/"):
            continue
        link = token.split(")", 1)[0].split("#", 1)[0]
        if link.endswith(".md"):
            links.add(link)
    return sorted(links)


def extract_graph_nodes(text: str) -> list[str]:
    nodes: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if '["' not in stripped or not stripped.endswith('"]'):
            continue
        nodes.add(stripped.split('["', 1)[1][:-2])
    return sorted(nodes)



def build_context(selected_tier: str | None) -> ArchitectureContext:
    tier_names = [selected_tier] if selected_tier else list(TIERS)
    architecture_text = read_architecture()
    tiers: dict[str, TierContext] = {}
    component_paths: set[str] = set()
    wit_contracts: dict[str, list[WitEntry]] = {}

    for tier_name in tier_names:
        components = collect_components(tier_name)
        contracts = collect_wit_contracts(tier_name)
        tiers[tier_name] = {
            "label": TIERS[tier_name]["label"],
            "directory": TIERS[tier_name]["directory"],
            "components": components,
            "wit_contracts": contracts,
        }
        component_paths.update(component["path"] for component in components)
        wit_contracts[tier_name] = contracts

    links = extract_component_links(architecture_text)
    missing_links = sorted(
        link for link in links if not (REPO_ROOT / link).is_file()
    )
    unlinked_components = sorted(component_paths.difference(links))
    unknown_links = sorted(set(links).difference(component_paths))
    ambiguous_wit_names = sorted(
        contract["path"]
        for contracts in wit_contracts.values()
        for contract in contracts
        if contract["name"] in {"fireball", "memory", "runtime", "interface"}
    )

    return {
        "architecture_document": ARCHITECTURE_PATH.relative_to(REPO_ROOT).as_posix(),
        "tiers": tiers,
        "graph_nodes": extract_graph_nodes(architecture_text),
        "wit_contracts": wit_contracts,
        "ambiguous_wit_names": ambiguous_wit_names,
        "component_links": links,
        "missing_links": missing_links,
        "unlinked_components": unlinked_components,
        "unknown_links": unknown_links,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect architecture Tier and component-link context."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )
    parser.add_argument(
        "--tier",
        choices=sorted(TIERS),
        help="Collect one Tier instead of the full component inventory.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    context = build_context(arguments.tier)
    if arguments.json:
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return 0

    print(f"architecture: {context['architecture_document']}")
    for tier_name, tier in context["tiers"].items():
        print(f"{tier_name}:")
        for component in tier["components"]:
            print(f"  - {component['path']}")
    print(f"graph_nodes: {len(context['graph_nodes'])}")
    print(f"wit_contracts: {sum(len(contracts) for contracts in context['wit_contracts'].values())}")
    print(f"ambiguous_wit_names: {len(context['ambiguous_wit_names'])}")
    print(f"component_links: {len(context['component_links'])}")
    print(f"missing_links: {len(context['missing_links'])}")
    print(f"unlinked_components: {len(context['unlinked_components'])}")
    print(f"unknown_links: {len(context['unknown_links'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
