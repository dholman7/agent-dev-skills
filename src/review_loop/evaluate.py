"""Measure the reviewer: seeded bugs it should catch, harmless changes it should leave alone.

Bug cases come from agent-eval-gate's bugs.yaml (each seeded bug is a one-file diff to
vendor-intake-app); clean cases from evals/review/clean.yaml. Titles are neutral so the PR
description never hints at the defect.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from review_loop.diff import make, parse
from review_loop.llm import LLM, LLMError
from review_loop.review import Review, review

LINE_TOLERANCE = 2


@dataclass
class Case:
    id: str
    kind: Literal["bug", "clean"]
    path: str
    diff: str
    after: str
    category: str


def load_cases(bugs: Path, clean: Path, target: Path) -> list[Case]:
    cases = []
    sources: tuple[tuple[Literal["bug", "clean"], Path, str], ...] = (
        ("bug", bugs, "bugs"),
        ("clean", clean, "changes"),
    )
    for kind, source, key in sources:
        for item in yaml.safe_load(source.read_text())[key]:
            before = (target / item["file"]).read_text()
            if before.count(item["old"]) != 1:
                raise ValueError(f"{item['id']}: `old` must match exactly once in {item['file']}")
            after = before.replace(item["old"], item["new"])
            cases.append(
                Case(
                    item["id"],
                    kind,
                    item["file"],
                    make(item["file"], before, after),
                    after,
                    item.get("category", "clean"),
                )
            )
    return cases


def _touches(result: Review, case: Case) -> bool:
    """An issue on, or within a couple of lines of, what the change added or deleted."""
    changed = parse(case.diff)[case.path].changed
    return any(
        f.label == "issue"
        and f.path == case.path
        and any(abs(f.line - n) <= LINE_TOLERANCE for n in changed)
        for f in result.findings
    )


def score(case: Case, result: Review) -> dict[str, Any]:
    blocking_issues = [f for f in result.findings if f.label == "issue" and f.blocking]
    return {
        "id": case.id,
        "kind": case.kind,
        "category": case.category,
        "caught": case.kind == "bug" and _touches(result, case),
        "caught_blocking": case.kind == "bug" and _touches(result, case) and bool(blocking_issues),
        "false_alarm": case.kind == "clean" and bool(blocking_issues),
        "comments": len(result.findings),
        "dropped": len(result.dropped),
        # Kept so scoring changes can be re-applied without paying for another run.
        "findings": [f.model_dump() for f in result.findings],
    }


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (0.0 if k == 0 else center - half), (1.0 if k == n else center + half)


def run(llm: LLM, cases: list[Case], *, verify_pass: bool) -> dict[str, Any]:
    rows = []
    for case in cases:
        try:
            result = review(
                llm,
                case.diff,
                {case.path: case.after},
                title=f"Update {Path(case.path).stem}",
                verify_pass=verify_pass,
            )
            rows.append(score(case, result))
        except LLMError as exc:
            rows.append(
                {
                    "id": case.id,
                    "kind": case.kind,
                    "error": str(exc),
                    "caught": False,
                    "caught_blocking": False,
                    "false_alarm": False,
                    "comments": 0,
                    "dropped": 0,
                }
            )
    bugs = [r for r in rows if r["kind"] == "bug"]
    clean = [r for r in rows if r["kind"] == "clean"]

    def rate(rows_: list[dict[str, Any]], key: str) -> dict[str, Any]:
        k, n = sum(bool(r[key]) for r in rows_), len(rows_)
        low, high = wilson(k, n)
        return {"value": k / n if n else 0.0, "k": k, "n": n, "low": low, "high": high}

    return {
        "verify": verify_pass,
        "bugs_caught": rate(bugs, "caught"),
        "bugs_caught_blocking": rate(bugs, "caught_blocking"),
        "false_alarms": rate(clean, "false_alarm"),
        "comments_per_clean_change": sum(r["comments"] for r in clean) / max(len(clean), 1),
        "dropped_by_checks": sum(r["dropped"] for r in rows),
        "cost_usd": round(llm.spend.cost_usd, 4),
        "rows": rows,
    }
