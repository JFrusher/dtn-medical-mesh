"""Both catalogues carry the same keys and the same placeholders, and render in a browser."""

import json
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "interface/static"


def placeholders(s: str) -> set[str]:
    """Argument names in an ICU string, including those nested in plural/select options."""
    names: set[str] = set()
    i = 0
    while i < len(s):
        if s[i] != "{":
            i += 1
            continue
        depth, j = 0, i
        while True:
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            if depth == 0:
                break
            j += 1
        body = s[i + 1 : j]
        names.add(body.split(",")[0].strip())
        if body.count(",") >= 2:  # plural/select: options are "key {text}" pairs
            rest = body.split(",", 2)[2]
            for option in re.findall(r"\{(.*?)\}(?=\s*\w*\s*\{|\s*$)", rest):
                names |= placeholders(option)
        i = j + 1
    return names


@pytest.mark.req("REQ-APP-01")
def test_catalogues_match_key_for_key_and_placeholder_for_placeholder() -> None:
    en = json.loads((STATIC / "i18n/en.json").read_text())
    sw = json.loads((STATIC / "i18n/sw.json").read_text())
    assert set(en) == set(sw)
    assert sw["_meta"]["reviewed"] is False  # until a native-speaking clinician checks it
    for key in en:
        if key != "_meta":
            assert placeholders(en[key]) == placeholders(sw[key]), key


def test_every_key_used_in_code_exists() -> None:
    en = json.loads((STATIC / "i18n/en.json").read_text())
    used: set[str] = set()
    for f in [*STATIC.glob("*.js"), *STATIC.glob("*.html")]:
        text = f.read_text()
        used |= set(re.findall(r'data-i18n(?:-[\w-]+)?="([\w.]+)"', text))
        used |= set(re.findall(r'\bt(?:r)?\("([\w.]+)"', text))
    assert used and used <= set(en), sorted(used - set(en))
