import json
import re
import tomllib
from pathlib import Path

import pytest

from validation.conftest import known_requirement_ids

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.req("REQ-SAFE-04")
def test_readme_and_ui_exclude_emergency_use() -> None:
    readme = (ROOT / "README.md").read_text()
    page = (ROOT / "interface/static/demo.html").read_text()
    i18n = ROOT / "interface/static/i18n"
    catalogues = [json.loads(f.read_text()) for f in i18n.glob("*.json")]
    assert "Not for emergencies" in readme and 'data-i18n="banner"' in page
    assert json.loads((ROOT / "interface/static/i18n/en.json").read_text())["banner"].startswith(
        "Not for emergencies"
    )
    warning = {"English": "Not for emergencies", "Kiswahili": "Si kwa dharura"}
    assert {c["_meta"]["language"] for c in catalogues} == set(warning)
    for c in catalogues:  # every language carries the warning, in its own words
        assert c["banner"].startswith(warning[c["_meta"]["language"]])
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


def test_soup_list_matches_locked_runtime_dependencies() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {p["name"]: p["version"] for p in lock["package"]}
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    runtime = {re.split(r"[<>=!~ ]", d)[0] for d in project["dependencies"]}
    soup = dict(
        re.findall(r"^\| ([a-z0-9-]+) \| ([0-9.]+) \|", (ROOT / "docs/soup.md").read_text(), re.M)
    )
    assert set(soup) == runtime
    for name, version in soup.items():
        assert locked[name] == version, (
            f"{name}: soup.md says {version}, uv.lock has {locked[name]}"
        )
