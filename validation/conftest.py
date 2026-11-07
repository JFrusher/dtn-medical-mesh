"""Shared pytest configuration: the `req` marker linking tests to docs/requirements.md."""

import re
from pathlib import Path

import pytest

REQUIREMENTS_FILE = Path(__file__).resolve().parent.parent / "docs" / "requirements.md"


def known_requirement_ids() -> set[str]:
    return set(re.findall(r"^\| (REQ-[A-Z]+-\d{2}) \|", REQUIREMENTS_FILE.read_text(), re.M))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "req(*ids): requirement IDs this test verifies")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    known = known_requirement_ids()
    for item in items:
        for marker in item.iter_markers("req"):
            unknown = set(marker.args) - known
            if unknown:
                raise pytest.UsageError(f"{item.nodeid} cites unknown requirement(s): {unknown}")
