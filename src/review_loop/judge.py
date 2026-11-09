"""Whether a review comment names the seeded defect, or only lands near it.

Location alone over-credits a reviewer: in a one-change diff, almost any `issue` comment sits
within a couple of lines of the bug. The judge reads the defect statement, the diff, and one
comment, and decides whether the comment identifies that defect. Its verdicts are only reported
next to its measured agreement with hand labels (Cohen's kappa), never alone.
"""

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

from review_loop.evaluate import touching, wilson
from review_loop.llm import LLM, LLMError

SYSTEM = """You grade code-review comments against a known defect that was deliberately seeded
into a one-file change. You see the defect statement, the diff, and one review comment.

The comment identifies the defect when a developer reading only the comment would find and fix
that defect: it names the behavior that broke (or the code that breaks it) and says why it's
wrong. It need not use the statement's words.

It does not identify the defect when it is about something else on the same lines (style,
naming, a different hypothetical bug), when it only says the line "looks suspicious" or asks a
question without naming the problem, or when it describes the change without saying it's wrong.

Answer for this one comment only."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rationale": {
            "type": "string",
            "description": "One or two sentences: what the comment says is wrong, and whether "
            "that is the seeded defect.",
        },
        "identifies": {"type": "boolean"},
    },
    "required": ["rationale", "identifies"],
    "additionalProperties": False,
}


def _comment(finding: dict[str, Any]) -> str:
    return (
        f"{finding['path']}:{finding['line']} ({finding['label']}"
        f"{', blocking' if finding.get('blocking') else ''})\n"
        f"{finding['subject']}\n\n{finding['discussion']}"
    )


def candidates(row: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """The findings location-based scoring credits on a bug row: the ones the judge rules on."""
    if row.get("kind") != "bug" or "diff" not in row:
        return []
    findings = row.get("findings", [])
    return [(i, findings[i]) for i in touching(findings, row["path"], row["diff"])]


def judge(llm: LLM, results: dict[str, Any]) -> dict[str, Any]:
    """Adds a verdict to every candidate finding, `identified` to every bug row, and a
    `bugs_identified` rate. Rows already judged are kept, so a rerun only pays for new ones."""
    # One client may judge several files; charge each file only for its own calls.
    spent_before = llm.spend.cost_usd
    for row in results["rows"]:
        for index, finding in candidates(row):
            if "judge" in finding:
                continue
            prompt = (
                f"<defect>\n{row['defect']}\n</defect>\n<diff>\n{row['diff']}</diff>\n"
                f"<comment>\n{_comment(finding)}\n</comment>"
            )
            try:
                finding["judge"] = llm.call(SYSTEM, prompt, SCHEMA)
            except LLMError as exc:
                finding["judge"] = {"identifies": False, "rationale": f"judge error: {exc}"}
            row["findings"][index] = finding
        if row.get("kind") == "bug":
            row["identified"] = any(f["judge"]["identifies"] for _, f in candidates(row))
    bugs = [r for r in results["rows"] if r.get("kind") == "bug"]
    k, n = sum(bool(r.get("identified")) for r in bugs), len(bugs)
    low, high = wilson(k, n)
    results["bugs_identified"] = {
        "value": k / n if n else 0.0,
        "k": k,
        "n": n,
        "low": low,
        "high": high,
    }
    spent = llm.spend.cost_usd - spent_before
    results["judge_cost_usd"] = round(results.get("judge_cost_usd", 0.0) + spent, 4)
    results["judge_models"] = sorted(set(results.get("judge_models", [])) | llm.spend.models)
    return results


def controls(runs: dict[str, dict[str, Any]], seed: int = 0) -> dict[str, Any]:
    """Negative controls, shaped like a results file: every judged comment again, but with
    another bug's defect statement. The right verdict is "no". A judge that says yes to
    everything scores perfectly on the real pairs, so without these its agreement means little.
    """
    rng = random.Random(seed)  # noqa: S311 - reproducible pairing, not security
    rows = []
    for name, results in sorted(runs.items()):
        bugs = [r for r in results["rows"] if r.get("kind") == "bug" and "diff" in r]
        for row in bugs:
            others = [r for r in bugs if r["id"] != row["id"]]
            if not others:
                continue
            for index, finding in candidates(row):
                other = rng.choice(others)
                rows.append(
                    {
                        "id": f"{name}~{row['id']}~{index}~vs~{other['id']}",
                        "kind": "bug",
                        "path": row["path"],
                        "diff": row["diff"],
                        "defect": other["defect"],
                        "findings": [{k: v for k, v in finding.items() if k != "judge"}],
                    }
                )
    return {"controls_for": sorted(runs), "rows": rows}


def pairs(runs: dict[str, dict[str, Any]]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Every judged (row, finding), keyed `run/case/index` so labels survive re-exports."""
    found = {}
    for name, results in runs.items():
        for row in results["rows"]:
            for index, finding in candidates(row):
                found[f"{name}/{row['id']}/{index}"] = (row, finding)
    return found


def label_id(key: str) -> str:
    """An opaque id for a pair: the key names the run and, for controls, says it's a control,
    which would tell the labeler the expected answer."""
    return "item-" + hashlib.sha256(key.encode()).hexdigest()[:10]


def export(runs: dict[str, dict[str, Any]], sample: int, seed: int = 0) -> str:
    """A Markdown file to label by hand, blind: no judge verdict, no run name, and no sign of
    which items are controls."""
    chosen = sorted(pairs(runs).items())
    random.Random(seed).shuffle(chosen)  # noqa: S311 - reproducible sample, not security
    lines = [
        "# review-loop judge: hand labels",
        "",
        "For each item, write `Label: yes` if the comment identifies the defect (a developer",
        "reading only the comment would find and fix it), or `Label: no` if it's about something",
        "else, only vague, or only describes the change. The judge's answers are not shown.",
        "",
    ]
    for key, (row, finding) in chosen[:sample]:
        lines += [
            f"## {label_id(key)}",
            "",
            f"**Defect:** {row['defect']}",
            "",
            "```diff",
            row["diff"].rstrip(),
            "```",
            "",
            "**Comment:**",
            "",
            *(f"> {line}".rstrip() for line in _comment(finding).splitlines()),
            "",
            "Label: ",
            "",
        ]
    return "\n".join(lines)


_ITEM = re.compile(r"^## (\S+)$.*?^Label:[ \t]*(\w*)[ \t]*$", re.M | re.S)


def read_labels(markdown: str) -> dict[str, bool]:
    """Labeled items only; an empty `Label:` is skipped, anything but yes/no is an error."""
    labels = {}
    for key, value in _ITEM.findall(markdown):
        if not value:
            continue
        if value.lower() not in ("yes", "no"):
            raise ValueError(f"{key}: label must be yes or no, got {value!r}")
        labels[key] = value.lower() == "yes"
    return labels


def cohen_kappa(pairs_: list[tuple[bool, bool]]) -> float:
    """Agreement beyond chance between two raters' yes/no labels."""
    n = len(pairs_)
    if n == 0:
        raise ValueError("no labeled pairs")
    observed = sum(a == b for a, b in pairs_) / n
    p_a, p_b = sum(a for a, _ in pairs_) / n, sum(b for _, b in pairs_) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def agreement(runs: dict[str, dict[str, Any]], labels: dict[str, bool]) -> dict[str, Any]:
    """`labels` are keyed by `label_id`, as `export` wrote them."""
    judged = {label_id(key): pair for key, pair in pairs(runs).items()}
    missing = sorted(set(labels) - set(judged))
    if missing:
        raise ValueError(f"labels for findings not in these results: {missing[:5]}")
    both = [(labels[k], bool(judged[k][1]["judge"]["identifies"])) for k in sorted(labels)]
    matrix = {
        "both_yes": sum(h and j for h, j in both),
        "both_no": sum(not h and not j for h, j in both),
        "human_yes_judge_no": sum(h and not j for h, j in both),
        "human_no_judge_yes": sum(not h and j for h, j in both),
    }
    return {
        "n": len(both),
        "kappa": cohen_kappa(both),
        "accuracy": sum(h == j for h, j in both) / len(both),
        **matrix,
    }


def load(paths: list[Path]) -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in paths}
