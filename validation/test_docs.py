from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.req("REQ-SAFE-04")
def test_readme_and_ui_exclude_emergency_use() -> None:
    readme = (ROOT / "README.md").read_text()
    page = (ROOT / "interface/static/demo.html").read_text()
    assert "Not for emergencies" in readme and "Not for emergencies" in page
    assert "Not certified" in readme
