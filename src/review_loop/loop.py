"""The PR loop: review what's new since the last review, re-check old findings, post."""

from dataclasses import dataclass, field
from typing import Any

from review_loop.diff import parse
from review_loop.github import GitHub, Thread
from review_loop.llm import LLM
from review_loop.review import Review, excerpt, render, review

RECHECK_SYSTEM = """Each item is a review comment left on an earlier version of a pull request,
with the code as it is now. Decide whether the problem the comment describes is still there.
fixed: the current code no longer has the problem. still_present: it does. unclear: the code
shown isn't enough to tell. Judge from the code, not from how confident the comment sounds."""

RECHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "status": {"type": "string", "enum": ["fixed", "still_present", "unclear"]},
                    "reason": {"type": "string"},
                },
                "required": ["index", "status", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


@dataclass
class Outcome:
    head: str
    incremental: bool
    review: Review | None = None
    fixed: list[Thread] = field(default_factory=list)
    still_open: list[Thread] = field(default_factory=list)
    skipped: str | None = None


def recheck(llm: LLM, threads: list[Thread], current: dict[str, str | None]) -> dict[int, str]:
    """Status per thread index. A deleted file counts as fixed: the code it pointed at is gone."""
    statuses: dict[int, str] = {}
    items = []
    for index, thread in enumerate(threads):
        content = current.get(thread.path)
        if content is None:
            statuses[index] = "fixed"
            continue
        code = excerpt(content, thread.line, radius=15)
        items.append(
            f'<item index="{index}">\n<comment>\n{thread.body}\n</comment>\n'
            f'<code path="{thread.path}">\n{code}\n</code>\n</item>'
        )
    if items:
        data = llm.call(RECHECK_SYSTEM, "\n\n".join(items), RECHECK_SCHEMA)
        statuses |= {r["index"]: r["status"] for r in data["results"]}
    return statuses


def run(
    llm: LLM, gh: GitHub, repo: str, number: int, *, post: bool, verify_pass: bool = True
) -> Outcome:
    pull = gh.pull(repo, number)
    head = pull["head"]["sha"]
    previous = gh.last_reviewed_sha(repo, number)
    if previous == head:
        return Outcome(head=head, incremental=False, skipped="this commit was already reviewed")

    # Re-review: only what changed since the commit last reviewed.
    diff_text = gh.compare(repo, previous, head) if previous else gh.diff(repo, number)
    paths = list(parse(diff_text))
    files = {p: c for p in paths if (c := gh.file(repo, p, head)) is not None}
    result = review(
        llm,
        diff_text,
        files,
        pull.get("title", ""),
        pull.get("body") or "",
        verify_pass=verify_pass,
    )
    outcome = Outcome(head=head, incremental=previous is not None, review=result)

    threads = gh.own_open_threads(repo, number)
    if threads:
        current = {t.path: files.get(t.path, gh.file(repo, t.path, head)) for t in threads}
        statuses = recheck(llm, threads, current)
        for index, thread in enumerate(threads):
            (outcome.fixed if statuses.get(index) == "fixed" else outcome.still_open).append(thread)

    if post:
        comments = [
            {"path": f.path, "line": f.line, "side": "RIGHT", "body": render(f)}
            for f in result.findings
        ]
        gh.post_review(repo, number, head, summary(outcome), comments)
        for thread in outcome.fixed:
            gh.reply_and_resolve(
                repo, number, thread, f"Resolved: the problem no longer appears in {head[:7]}."
            )
    return outcome


def summary(outcome: Outcome) -> str:
    if outcome.review is None:
        raise ValueError("no review to summarize")
    findings = outcome.review.findings
    blocking = sum(f.blocking for f in findings)
    scope = "new commits since the last review" if outcome.incremental else "the full diff"
    lines = [
        f"**review-loop** reviewed {scope}: {len(findings)} comment(s), {blocking} blocking.",
        f"Findings dropped after citation checks and verification: {len(outcome.review.dropped)}.",
    ]
    if outcome.fixed or outcome.still_open:
        lines.append(
            f"Earlier comments: {len(outcome.fixed)} now fixed and resolved, "
            f"{len(outcome.still_open)} still open."
        )
    return "\n".join(lines)
