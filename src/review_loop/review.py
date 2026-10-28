"""Review a diff, check every citation, then have a second pass re-derive each finding."""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from review_loop.diff import FileDiff, nearest, parse
from review_loop.llm import LLM

Label = Literal["issue", "suggestion", "nitpick", "question"]
MARKER = "<!-- review-loop -->"
RETARGET_DISTANCE = 5
EXCERPT_RADIUS = 8


class Finding(BaseModel):
    path: str
    line: int
    label: Label
    blocking: bool
    subject: str
    discussion: str
    evidence: str
    suggestion: str = ""


@dataclass
class Review:
    findings: list[Finding]
    # (finding, why it was dropped): the audit trail for every finding that didn't ship.
    dropped: list[tuple[Finding, str]] = field(default_factory=list)
    adjusted: list[str] = field(default_factory=list)


REVIEW_SYSTEM = """You review a pull request diff. Report defects a careful senior engineer would
flag: wrong behavior, security problems (authorization, injection, secrets, tenant isolation),
broken error handling, missing or weakened tests, and breaking API changes.

For each finding:
- path and line: the file and the line number in the NEW version where the problem is. Only
  lines inside the diff can carry a comment.
- evidence: copy the exact line(s) of code the finding is about, character for character.
- label: issue (a defect), suggestion (a better way), nitpick (minor style), question.
- blocking: true only for a real defect that should stop the merge.
- suggestion: literal code that replaces exactly the cited line, indented like it, when you
  can name the fix. Never prose or instructions; GitHub commits it as-is. Otherwise "".

Report nothing rather than speculate. An empty list is a good review when the change is fine."""

FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "line": {"type": "integer"},
        "label": {"type": "string", "enum": ["issue", "suggestion", "nitpick", "question"]},
        "blocking": {"type": "boolean"},
        "subject": {"type": "string", "description": "One line: the problem, not the fix."},
        "discussion": {"type": "string", "description": "Why it matters, briefly."},
        "evidence": {"type": "string"},
        "suggestion": {"type": "string"},
    },
    "required": [
        "path",
        "line",
        "label",
        "blocking",
        "subject",
        "discussion",
        "evidence",
        "suggestion",
    ],
    "additionalProperties": False,
}
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"findings": {"type": "array", "items": FINDING_SCHEMA}},
    "required": ["findings"],
    "additionalProperties": False,
}

VERIFY_SYSTEM = """You check another reviewer's findings against the code. Re-derive each finding
from the code excerpt and the diff yourself. Do not trust the finding's own reasoning.

For each finding, return one verdict:
- confirm: the code shows the problem as described.
- demote: the problem is real but does not need to block the merge.
- retarget: the problem is real but sits on a different line; give that line.
- drop: the code does not show the problem, or the finding misreads it.

A confident wrong comment costs a reviewer's trust. When in doubt, drop."""

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": ["confirm", "demote", "retarget", "drop"],
                    },
                    "line": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "verdict", "line", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def excerpt(content: str, line: int, radius: int = EXCERPT_RADIUS) -> str:
    lines = content.splitlines()
    start, end = max(line - radius, 1), min(line + radius, len(lines))
    return "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))


def _locate(content: str, line: int, evidence: str) -> int | None:
    """The line where the quoted evidence actually is, preferring the one closest to `line`."""
    wanted = [w.strip() for w in evidence.strip().splitlines() if w.strip()]
    if not wanted:
        return None
    lines = [ln.strip() for ln in content.splitlines()]
    hits = [i + 1 for i, text in enumerate(lines) if wanted[0] in text]
    return min(hits, key=lambda n: abs(n - line)) if hits else None


def _replaces(suggestion: str, content: str, line: int) -> bool:
    """A GitHub suggestion replaces the commented line, so it must start at that line's
    indentation. Catches prose written into the suggestion field; top-level lines can't be told
    apart this way and pass."""
    cited = content.splitlines()[line - 1]
    indent = cited[: len(cited) - len(cited.lstrip())]
    first = next(s for s in suggestion.splitlines() if s.strip())
    return first[: len(first) - len(first.lstrip())] == indent


def check_citations(
    findings: list[Finding], diffs: dict[str, FileDiff], files: dict[str, str]
) -> Review:
    """Deterministic: the file must be in the diff, the quote must exist, the line must be
    commentable. Cheap, and catches the most common way a model's finding goes wrong."""
    result = Review(findings=[])
    for finding in findings:
        diff, content = diffs.get(finding.path), files.get(finding.path)
        if diff is None or content is None:
            result.dropped.append((finding, "cites a file the diff doesn't change"))
            continue
        actual = _locate(content, finding.line, finding.evidence)
        if actual is None:
            result.dropped.append((finding, "the quoted code isn't in the file"))
            continue
        if actual != finding.line:
            result.adjusted.append(
                f"{finding.path}: line {finding.line} -> {actual} (where the quote is)"
            )
            finding = finding.model_copy(update={"line": actual})
        if finding.line not in diff.commentable:
            target = nearest(diff.commentable, finding.line)
            if target is None or abs(target - finding.line) > RETARGET_DISTANCE:
                result.dropped.append((finding, "points outside the diff"))
                continue
            # The suggestion was written for the quoted line; GitHub would apply it to this one.
            finding = finding.model_copy(update={"line": target, "suggestion": ""})
        if finding.suggestion.strip() and not _replaces(finding.suggestion, content, finding.line):
            result.adjusted.append(
                f"{finding.path}:{finding.line}: suggestion removed (not code for the cited line)"
            )
            finding = finding.model_copy(update={"suggestion": ""})
        result.findings.append(finding)
    return result


def verify(llm: LLM, checked: Review, diffs: dict[str, FileDiff], files: dict[str, str]) -> Review:
    if not checked.findings:
        return checked
    blocks = []
    for index, f in enumerate(checked.findings):
        weight = "blocking" if f.blocking else "non-blocking"
        blocks.append(
            f'<finding index="{index}">\n{f.label} ({weight}): '
            f"{f.subject}\n{f.discussion}\nCited: {f.path}:{f.line}\n"
            f"<code>\n{excerpt(files[f.path], f.line)}\n</code>\n"
            f"<diff>\n{diffs[f.path].text}</diff>\n</finding>"
        )
    data = llm.call(VERIFY_SYSTEM, "\n\n".join(blocks), VERIFY_SCHEMA)
    verdicts = {v["index"]: v for v in data["verdicts"]}
    result = Review(findings=[], dropped=list(checked.dropped), adjusted=list(checked.adjusted))
    for index, finding in enumerate(checked.findings):
        verdict = verdicts.get(index)
        if verdict is None or verdict["verdict"] == "drop":
            reason = verdict["reason"] if verdict else "the verifier returned no verdict"
            result.dropped.append((finding, f"verifier: {reason}"))
        elif verdict["verdict"] == "demote":
            result.findings.append(finding.model_copy(update={"blocking": False}))
            result.adjusted.append(f"{finding.path}:{finding.line} demoted: {verdict['reason']}")
        elif verdict["verdict"] == "retarget":
            line = verdict["line"]
            if line in diffs[finding.path].commentable:
                moved = {"line": line} if line == finding.line else {"line": line, "suggestion": ""}
                result.findings.append(finding.model_copy(update=moved))
                result.adjusted.append(f"{finding.path}: retargeted to line {line}")
            else:
                result.dropped.append(
                    (finding, f"verifier retargeted outside the diff (line {line})")
                )
        else:
            result.findings.append(finding)
    return result


def review(
    llm: LLM,
    diff_text: str,
    files: dict[str, str],
    title: str = "",
    body: str = "",
    *,
    verify_pass: bool = True,
) -> Review:
    """`files` maps each changed path to its full NEW content."""
    diffs = parse(diff_text)
    context = "\n\n".join(
        f'<file path="{path}">\n{content}\n</file>'
        for path, content in files.items()
        if path in diffs
    )
    prompt = (
        f"<pull_request>\nTitle: {title or '(none)'}\n{body}\n</pull_request>\n"
        f"<diff>\n{diff_text}</diff>\n<changed_files_after>\n{context}\n</changed_files_after>"
    )
    raw = llm.call(REVIEW_SYSTEM, prompt, REVIEW_SCHEMA)["findings"]
    checked = check_citations([Finding.model_validate(f) for f in raw], diffs, files)
    return verify(llm, checked, diffs, files) if verify_pass else checked


def render(finding: Finding) -> str:
    """A Conventional Comments body: `label (decoration): subject`, discussion, optional fix."""
    decoration = "blocking" if finding.blocking else "non-blocking"
    text = f"{finding.label} ({decoration}): {finding.subject}\n\n{finding.discussion}"
    if finding.suggestion.strip():
        text += f"\n\n```suggestion\n{finding.suggestion.rstrip()}\n```"
    return f"{text}\n\n{MARKER}"
