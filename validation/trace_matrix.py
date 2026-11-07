"""Requirements -> tests -> results (spec §13). Reports test coverage of requirements.

It does not report, and must never be read as, regulatory compliance.

    uv run python validation/trace_matrix.py [--fast] [--out docs/trace_matrix.md]

Exits non-zero if any active requirement has no test or a failing test.
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQ_ROW = re.compile(r"^\| (REQ-[A-Z]+-\d{2}) \| (.+?) \| (.+?) \|$", re.M)


class Collector:
    def __init__(self) -> None:
        self.tests: dict[str, list[str]] = defaultdict(list)
        self.outcome: dict[str, str] = {}

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            for marker in item.iter_markers("req"):
                for req in marker.args:
                    self.tests[req].append(item.nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == "call" or report.outcome != "passed":
            prev = self.outcome.get(report.nodeid, "passed")
            self.outcome[report.nodeid] = report.outcome if prev == "passed" else prev


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fast", action="store_true", help="skip the slow crash and browser tests")
    p.add_argument("--out", type=Path, help="also write the matrix as Markdown here")
    args = p.parse_args(argv)
    reqs = REQ_ROW.findall((ROOT / "docs/requirements.md").read_text())
    collector = Collector()
    extra = (
        ["--ignore=validation/crash", "--ignore=validation/interface/test_ui.py"]
        if args.fast
        else []
    )
    pytest.main(
        ["-q", "-p", "no:cacheprovider", str(ROOT / "validation"), *extra], plugins=[collector]
    )

    lines = [
        "# Requirements trace matrix",
        "",
        "Test coverage of requirements. **Not a statement of regulatory compliance.**",
        "",
        "| Requirement | Tests | Result |",
        "|---|---|---|",
    ]
    problems = 0
    for req, text, _ in reqs:
        tests = collector.tests.get(req, [])
        if text.startswith("Retired"):
            result = "retired"
        elif not tests:
            result, problems = "**NO TEST**", problems + 1
        elif all(collector.outcome.get(t) == "passed" for t in tests):
            result = "pass"
        else:
            result, problems = "**FAIL**", problems + 1
        lines.append(f"| {req} | {len(tests)} | {result} |")
    active = [r for r in reqs if not r[1].startswith("Retired")]
    lines += [
        "",
        f"{len(active) - problems}/{len(active)} active requirements covered by passing tests.",
    ]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        args.out.write_text(report)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
