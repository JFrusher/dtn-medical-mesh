"""The emulator's physical and compute parameters, each with its source (spec §12.2)."""

from dataclasses import dataclass
from pathlib import Path

import yaml

PATH = Path(__file__).parent / "assumptions.yaml"
SOURCES = {"published", "estimate", "measured"}


@dataclass(frozen=True)
class Assumption:
    value: float
    unit: str
    source: str
    ref: str


def load(path: Path = PATH) -> dict[str, Assumption]:
    """All assumptions keyed "group.name". Fails on a missing field or an unknown source."""
    out: dict[str, Assumption] = {}
    for group, entries in yaml.safe_load(path.read_text()).items():
        for name, a in entries.items():
            if set(a) != {"value", "unit", "source", "ref"} or a["source"] not in SOURCES:
                raise ValueError(f"assumption {group}.{name}: needs value, unit, source, ref")
            out[f"{group}.{name}"] = Assumption(a["value"], str(a["unit"]), a["source"], a["ref"])
    return out


ASSUMPTIONS = load()


def group(name: str) -> dict[str, float]:
    prefix = f"{name}."
    return {k[len(prefix) :]: a.value for k, a in ASSUMPTIONS.items() if k.startswith(prefix)}
