"""The requirements list parses, and IDs are unique."""

import re

from validation.conftest import REQUIREMENTS_FILE, known_requirement_ids


def test_requirement_ids_parse_and_are_unique() -> None:
    rows = re.findall(r"^\| (REQ-[A-Z]+-\d{2}) \|", REQUIREMENTS_FILE.read_text(), re.M)
    assert len(rows) == len(set(rows)), "duplicate requirement IDs"
    assert "REQ-SAFE-01" in known_requirement_ids()
