"""The PR loop against a fake GitHub: first review, re-review, and resolving only its own threads."""

import json
from pathlib import Path
from typing import Any

import httpx

from review_loop import evaluate, loop
from review_loop.diff import make
from review_loop.github import GitHub
from review_loop.review import MARKER
from tests.test_review import AFTER, BEFORE, DIFF, PATH, Scripted, _finding

REPO = "owner/app"


class FakeGitHub:
    def __init__(self, reviewed_sha: str | None = None) -> None:
        self.reviewed_sha = reviewed_sha
        self.posted: list[dict[str, Any]] = []
        self.replies: list[str] = []
        self.resolved: list[str] = []
        self.compared: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        accept = request.headers.get("accept", "")
        if path == f"/repos/{REPO}/pulls/5" and "diff" in accept:
            return httpx.Response(200, text=DIFF)
        if path == f"/repos/{REPO}/pulls/5":
            return httpx.Response(
                200, json={"head": {"sha": "bbbbbbb"}, "title": "Tidy create", "body": ""}
            )
        if path.startswith(f"/repos/{REPO}/compare/"):
            self.compared.append(path)
            return httpx.Response(200, text=DIFF)
        if path == f"/repos/{REPO}/contents/{PATH}":
            return httpx.Response(200, text=AFTER)
        if path == f"/repos/{REPO}/pulls/5/reviews" and request.method == "GET":
            body = f"<!-- review-loop sha={self.reviewed_sha} -->" if self.reviewed_sha else "LGTM"
            return httpx.Response(200, json=[{"body": body}])
        if path == f"/repos/{REPO}/pulls/5/reviews":
            self.posted.append(json.loads(request.content))
            return httpx.Response(200, json={})
        if path.endswith("/replies"):
            self.replies.append(path)
            return httpx.Response(201, json={})
        if path == "/graphql":
            payload = json.loads(request.content)
            if "resolveReviewThread" in payload["query"]:
                self.resolved.append(payload["variables"]["id"])
                return httpx.Response(200, json={"data": {}})
            nodes = [
                {
                    "id": "ours",
                    "isResolved": False,
                    "comments": {
                        "nodes": [
                            {
                                "databaseId": 1,
                                "path": PATH,
                                "line": 3,
                                "originalLine": 3,
                                "body": f"issue: old bug\n\n{MARKER}",
                            }
                        ]
                    },
                },
                {
                    "id": "human",
                    "isResolved": False,
                    "comments": {
                        "nodes": [
                            {
                                "databaseId": 2,
                                "path": PATH,
                                "line": 2,
                                "originalLine": 2,
                                "body": "please rename this",
                            }
                        ]
                    },
                },
            ]
            return httpx.Response(
                200,
                json={"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes}}}}},
            )
        return httpx.Response(404)


def _gh(fake: FakeGitHub) -> GitHub:
    return GitHub("token", transport=httpx.MockTransport(fake))


def test_first_review_posts_comments_with_a_resumable_marker() -> None:
    fake = FakeGitHub()
    llm = Scripted(
        {"findings": [_finding()]},
        {"verdicts": [{"index": 0, "verdict": "confirm", "line": 3, "reason": "ok"}]},
        {"results": [{"index": 0, "status": "still_present", "reason": "unchanged"}]},
    )

    outcome = loop.run(llm, _gh(fake), REPO, 5, post=True)

    assert not outcome.incremental and fake.compared == []
    [review] = fake.posted
    assert review["event"] == "COMMENT"  # never approves or requests changes
    assert review["comments"][0]["line"] == 3
    assert review["comments"][0]["body"].startswith("issue (blocking):")
    assert "<!-- review-loop sha=bbbbbbb -->" in review["body"]


def test_re_review_compares_new_commits_and_resolves_only_its_own_fixed_thread() -> None:
    fake = FakeGitHub(reviewed_sha="aaaaaaa")
    llm = Scripted(
        {"findings": []},  # nothing new; verify is skipped when there are no findings
        {"results": [{"index": 0, "status": "fixed", "reason": "201 restored"}]},
    )

    outcome = loop.run(llm, _gh(fake), REPO, 5, post=True)

    assert outcome.incremental
    assert fake.compared == [f"/repos/{REPO}/compare/aaaaaaa...bbbbbbb"]
    assert fake.resolved == ["ours"]  # the person's thread is never returned, let alone resolved
    assert fake.replies == [
        f"/repos/{REPO}/pulls/comments/1/replies".replace("pulls/comments", "pulls/5/comments")
    ]


def test_an_already_reviewed_commit_is_skipped() -> None:
    outcome = loop.run(Scripted(), _gh(FakeGitHub(reviewed_sha="bbbbbbb")), REPO, 5, post=True)
    assert outcome.skipped == "this commit was already reviewed"


def test_eval_builds_cases_and_scores_catches_and_false_alarms(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / PATH).write_text(BEFORE)
    bugs = tmp_path / "bugs.yaml"
    bugs.write_text(
        f"bugs:\n  - id: b1\n    category: status_codes\n    file: {PATH}\n    old: 'return 201'\n    new: 'return 200'\n"
    )
    clean = tmp_path / "clean.yaml"
    clean.write_text(
        f"changes:\n  - id: c1\n    file: {PATH}\n    old: 'return 1'\n    new: 'return 1  # constant'\n"
    )

    cases = evaluate.load_cases(bugs, clean, tmp_path)
    bug_case = next(c for c in cases if c.kind == "bug")
    assert bug_case.diff == make(PATH, BEFORE, BEFORE.replace("return 201", "return 200"))

    llm = Scripted(
        {"findings": [_finding(line=4, evidence="return 200")]},
        {"verdicts": [{"index": 0, "verdict": "confirm", "line": 4, "reason": "ok"}]},
        {
            "findings": [
                _finding(path=PATH, line=8, evidence="return 1  # constant", subject="magic number")
            ]
        },
        {"verdicts": [{"index": 0, "verdict": "confirm", "line": 8, "reason": "ok"}]},
    )
    report = evaluate.run(llm, cases, verify_pass=True)

    assert report["bugs_caught"]["k"] == 1
    assert report["false_alarms"]["k"] == 1  # a blocking issue on a harmless change
    assert report["clean_flagged"]["k"] == 1


def test_eval_without_bugs_runs_only_clean_and_counts_non_blocking_issues(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / PATH).write_text(BEFORE)
    clean = tmp_path / "clean.yaml"
    clean.write_text(
        f"changes:\n  - id: c1\n    file: {PATH}\n    old: 'return 1'\n    new: 'return 1  # constant'\n"
    )

    cases = evaluate.load_cases(None, clean, tmp_path)
    llm = Scripted(
        {"findings": [_finding(line=8, evidence="return 1  # constant", blocking=False)]},
        {"verdicts": [{"index": 0, "verdict": "confirm", "line": 8, "reason": "ok"}]},
    )
    report = evaluate.run(llm, cases, verify_pass=True)

    assert [c.kind for c in cases] == ["clean"]
    assert report["bugs_caught"]["n"] == 0
    assert report["false_alarms"]["k"] == 0  # not blocking...
    assert report["clean_flagged"]["k"] == 1  # ...but still a claimed defect on correct code
