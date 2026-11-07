import re
from pathlib import Path

import pytest

from validation.conftest import known_requirement_ids

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.req("REQ-SAFE-04")
def test_readme_and_ui_exclude_emergency_use() -> None:
    readme = (ROOT / "README.md").read_text()
    page = (ROOT / "interface/static/demo.html").read_text()
    assert "Not for emergencies" in readme and "Not for emergencies" in page
    assert "Not certified" in readme


def test_every_hazard_cites_existing_requirements() -> None:
    log = (ROOT / "docs/hazard_log.md").read_text()
    rows = re.findall(r"^\| (HAZ-\d{2}) \|(.+)$", log, re.M)
    assert len(rows) >= 10
    known = known_requirement_ids()
    for haz, row in rows:
        cited = set(re.findall(r"REQ-[A-Z]+-\d{2}", row))
        assert cited, f"{haz} has no control"
        assert cited <= known, f"{haz} cites unknown {cited - known}"
