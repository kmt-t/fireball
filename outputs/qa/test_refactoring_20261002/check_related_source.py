from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.source import SourceFacade

config = Config.load("spec-integrator.yaml")
for group in config.source_verification.groups.values():
    for check in group.checks:
        if check.id == "run_tests":
            check.enabled = (
                False  # Related pytest files are executed separately; never run all suites.
            )
files = Path(__file__).with_name("changed_source_files.txt").read_text().splitlines()
results = SourceFacade(config).check(group="pysim", file_paths=files)
for result in results:
    print(result.group, result.status, result.files_evaluated)
    for issue in result.issues:
        print(issue.severity, issue.rule, issue.file_path, issue.line, issue.message)
assert all(result.status == "PASS" for result in results)
