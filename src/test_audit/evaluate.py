"""Score test-audit against known gaps (recall) and against the same suite after they were fixed
(every finding there is a candidate false alarm, to be checked by hand)."""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from test_audit.audit import Audit, Finding


@dataclass(frozen=True)
class Instance:
    gap: str
    rule: str
    keywords: tuple[str, ...]
    # Each (path, lines) is a place the instance may be cited; None lines: anywhere in the file.
    places: tuple[tuple[str, tuple[int, int] | None], ...]


def load_truth(path: Path) -> tuple[dict[str, Any], list[Instance]]:
    truth = yaml.safe_load(path.read_text())
    # An all-digit short SHA such as 8315131 parses as an int.
    for ref in ("gap_commit", "clean_commit"):
        truth[ref] = str(truth[ref])
    tolerance = int(truth.get("tolerance", 3))
    instances = []
    for gap in truth["gaps"]:
        keywords = tuple(k.lower() for k in gap["keywords"])

        def span(at: dict[str, Any]) -> list[tuple[int, int] | None]:
            if "range" in at:
                return [(at["range"][0], at["range"][1])]
            if "lines" in at:
                return [(n - tolerance, n + tolerance) for n in at["lines"]]
            return [None]

        if gap.get("any_of"):
            places = tuple((at["path"], s) for at in gap["at"] for s in span(at))
            instances.append(Instance(gap["id"], gap["rule"], keywords, places))
        else:
            for at in gap["at"]:
                for s in span(at):
                    instances.append(Instance(gap["id"], gap["rule"], keywords, ((at["path"], s),)))
    return truth, instances


def hits(finding: Finding, instance: Instance) -> bool:
    text = f"{finding.rule} {finding.problem}".lower()
    # "new" says nothing about which gap, so a new-gap finding must name it in words.
    by_rule = instance.rule != "new" and finding.rule == instance.rule
    names_it = by_rule or any(k in text for k in instance.keywords)
    return names_it and any(
        finding.path == path and (lines is None or lines[0] <= finding.line <= lines[1])
        for path, lines in instance.places
    )


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (0.0 if k == 0 else center - half), (1.0 if k == n else center + half)


def score(result: Audit, instances: list[Instance]) -> dict[str, Any]:
    found = [i for i in instances if any(hits(f, i) for f in result.findings)]
    gaps = sorted({i.gap for i in instances})
    gaps_found = sorted({i.gap for i in found})
    unmatched = [f for f in result.findings if not any(hits(f, i) for i in instances)]
    low, high = wilson(len(found), len(instances))
    return {
        "instances_found": {"k": len(found), "n": len(instances), "low": low, "high": high},
        "gaps_found": {"k": len(gaps_found), "n": len(gaps)},
        "missed": sorted({f"{i.gap} {i.places[0][0]}" for i in instances if i not in found}),
        "findings": len(result.findings),
        # Every finding and the gaps it hits, so scoring changes can be re-applied for free.
        "all": [
            {**f.model_dump(), "hits": sorted({i.gap for i in instances if hits(f, i)})}
            for f in result.findings
        ],
        # Real but unlisted problems, or false alarms: each needs a human look.
        "unmatched": [f.model_dump() for f in unmatched],
        "dropped": [{"finding": f.model_dump(), "reason": r} for f, r in result.dropped],
    }
