"""The review pipeline with a scripted model: citation checks, verification, rendering."""

from collections.abc import Iterator
from typing import Any

from review_loop.diff import make, nearest, parse
from review_loop.llm import Spend
from review_loop.review import MARKER, Finding, check_citations, render, review

PATH = "app/routes.py"
BEFORE = (
    "def create():\n    validate()\n    save()\n    return 201\n\n\ndef other():\n    return 1\n"
)
AFTER = "def create():\n    save()\n    return 200\n\n\ndef other():\n    return 1\n"
DIFF = make(PATH, BEFORE, AFTER)


class Scripted:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies: Iterator[dict[str, Any]] = iter(replies)
        self.spend = Spend()
        self.prompts: list[str] = []

    def call(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return next(self.replies)


def _finding(**overrides: Any) -> dict[str, Any]:
    base = {
        "path": PATH,
        "line": 3,
        "label": "issue",
        "blocking": True,
        "subject": "create now answers 200",
        "discussion": "Clients expect 201.",
        "evidence": "return 200",
        "suggestion": "    return 201",
    }
    return base | overrides


def test_parse_finds_added_and_commentable_lines() -> None:
    files = parse(DIFF)
    assert set(files) == {PATH}
    assert files[PATH].added == {3}
    assert {1, 2, 3}.issubset(files[PATH].commentable)
    assert nearest({2, 9}, 4) == 2


def test_citation_check_fixes_lines_and_drops_what_it_cannot_ground() -> None:
    findings = [
        Finding.model_validate(_finding(line=1)),  # right quote, wrong line -> moved to line 3
        Finding.model_validate(_finding(evidence="return 404")),  # quote isn't in the file
        Finding.model_validate(_finding(path="other.py")),  # file not in the diff
    ]

    result = check_citations(findings, parse(DIFF), {PATH: AFTER})

    assert [f.line for f in result.findings] == [3]
    assert [reason for _, reason in result.dropped] == [
        "the quoted code isn't in the file",
        "cites a file the diff doesn't change",
    ]


def test_verifier_verdicts_confirm_demote_and_drop() -> None:
    llm = Scripted(
        {
            "findings": [
                _finding(),
                _finding(subject="validate() was removed", evidence="save()", line=2),
                _finding(
                    subject="speculative",
                    evidence="def create():",
                    line=1,
                    blocking=False,
                    label="question",
                ),
            ]
        },
        {
            "verdicts": [
                {"index": 0, "verdict": "confirm", "line": 3, "reason": "201 to 200 is visible"},
                {"index": 1, "verdict": "demote", "line": 2, "reason": "real, but tests cover it"},
                {
                    "index": 2,
                    "verdict": "drop",
                    "line": 1,
                    "reason": "nothing in the code supports it",
                },
            ]
        },
    )

    result = review(llm, DIFF, {PATH: AFTER})

    assert [(f.subject, f.blocking) for f in result.findings] == [
        ("create now answers 200", True),
        ("validate() was removed", False),
    ]
    assert result.dropped[0][1].startswith("verifier: nothing in the code")
    assert (
        "3:     return 200" in llm.prompts[1]
    )  # the verifier saw numbered code, not the claim alone


def test_no_verify_skips_the_second_call() -> None:
    llm = Scripted({"findings": [_finding()]})
    assert len(review(llm, DIFF, {PATH: AFTER}, verify_pass=False).findings) == 1
    assert len(llm.prompts) == 1


def test_render_is_a_conventional_comment_with_a_suggestion_block() -> None:
    body = render(Finding.model_validate(_finding()))
    assert body.startswith("issue (blocking): create now answers 200\n\nClients expect 201.")
    assert "```suggestion\n    return 201\n```" in body
    assert body.endswith(MARKER)


def test_a_pure_deletion_is_located_between_its_neighbors() -> None:
    # Regression: deletion-only diffs have no added lines, so no finding could ever "catch" them.
    files = parse(make(PATH, BEFORE, BEFORE.replace("    validate()\n", "")))
    assert files[PATH].added == set()
    assert files[PATH].deleted_near == {1, 2}
    assert files[PATH].changed == {1, 2}


def test_suggestions_that_cannot_replace_the_cited_line_are_removed() -> None:
    findings = [
        Finding.model_validate(_finding()),  # code at the cited line's indentation: kept
        Finding.model_validate(_finding(suggestion="Return 201 here instead.")),  # prose
    ]

    result = check_citations(findings, parse(DIFF), {PATH: AFTER})

    assert [f.suggestion for f in result.findings] == ["    return 201", ""]
    assert result.adjusted == [f"{PATH}:3: suggestion removed (not code for the cited line)"]
    assert "suggestion" not in render(result.findings[1])


def test_retargeting_a_finding_drops_its_suggestion() -> None:
    before = "".join(f"    v{i} = {i}\n" for i in range(1, 13))
    after = before.replace("    v2 = 2\n", "    v2 = 20\n")
    diffs = parse(make("a.py", before, after))  # commentable: lines 1-5
    finding = Finding.model_validate(
        _finding(path="a.py", line=7, evidence="v7 = 7", suggestion="    v7 = 70")
    )

    [moved] = check_citations([finding], diffs, {"a.py": after}).findings

    # It now sits on line 5; "v7 = 70" committed there would overwrite v5.
    assert (moved.line, moved.suggestion) == (5, "")
