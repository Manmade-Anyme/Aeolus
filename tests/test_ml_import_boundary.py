"""Constraint test (TASK-021 ADR Definition of Done): the engine must run
identically whether the ML module is present, absent, or broken, which is
only true if no engine-side module imports aeolus.ml at runtime. Scheduler
references MLHooks only through a structural Protocol (see
aeolus.scheduler.scheduler.MLHooksProtocol) precisely so this holds.
"""

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "aeolus"
RESTRICTED_PACKAGES = ("engine", "signals", "ingestion", "outlook", "explain", "output", "jobs")


def _imports_aeolus_ml(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "aeolus.ml" or alias.name.startswith("aeolus.ml.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "aeolus.ml" or node.module.startswith("aeolus.ml.")):
                return True
    return False


def test_no_runtime_import_of_aeolus_ml_in_restricted_packages():
    offenders = []
    for package in RESTRICTED_PACKAGES:
        package_dir = SRC_ROOT / package
        assert package_dir.is_dir(), f"expected package directory {package_dir} to exist"
        for path in package_dir.rglob("*.py"):
            if _imports_aeolus_ml(path):
                offenders.append(str(path))
    assert offenders == []


def test_scheduler_module_does_not_import_aeolus_ml():
    scheduler_path = SRC_ROOT / "scheduler" / "scheduler.py"
    assert not _imports_aeolus_ml(scheduler_path)
