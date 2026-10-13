"""関係するファイルだけを正本の静的検査へ渡し、回帰テストは別途実行する。"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.source.facade import SourceFacade

root = Path(__file__).resolve().parents[5]
initial = json.loads((Path(__file__).parent / "initial_sources.json").read_text())
changed = [
    name
    for name, digest in initial.items()
    if (root / name).is_file() and hashlib.sha256((root / name).read_bytes()).hexdigest() != digest
]
config = Config.load(root / "spec-integrator.yaml")
for group in config.source_verification.groups.values():
    for rule in group.checks:
        if rule.id == "run_tests":
            rule.enabled = False
facade = SourceFacade(config)
results = []
for group, files in (
    (
        "pysim",
        [
            name
            for name in changed
            if name.startswith("experiments/pysim/") and name.endswith(".py")
        ],
    ),
    ("cpp", [name for name in changed if name.endswith((".cxx", ".hxx"))]),
    ("formal", [name for name in changed if "/formal/" in name and name.endswith(".py")]),
):
    if not files:
        continue
    print(group, files)
    for result in facade.check(group=group, file_paths=files):
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        results.append(result)
raise SystemExit(1 if any(result.status == "FAIL" for result in results) else 0)
