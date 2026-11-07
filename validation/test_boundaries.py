"""Structural rules enforced on imports.

- node/ never imports endpoint/ or interface/: no Pi can hold plaintext crypto.
- Production packages never import simulation/: seeded entropy is unreachable.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN: dict[str, set[str]] = {
    "node": {"endpoint", "interface", "simulation", "validation"},
    "endpoint": {"interface", "simulation", "validation"},
    "interface": {"simulation", "validation"},
}


def imported_top_levels(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def violations(package: str) -> list[str]:
    found = []
    for path in sorted((ROOT / package).rglob("*.py")):
        bad = imported_top_levels(path) & FORBIDDEN[package]
        found += [f"{path.relative_to(ROOT)} imports {name}" for name in sorted(bad)]
    return found


@pytest.mark.req("REQ-SEC-02", "REQ-SEC-13")
@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_import_boundaries(package: str) -> None:
    assert violations(package) == []


def test_checker_detects_violation(tmp_path: Path) -> None:
    bad = tmp_path / "x.py"
    bad.write_text("from endpoint.crypto import seal\nimport simulation.virtual_hw\n")
    assert imported_top_levels(bad) == {"endpoint", "simulation"}
