"""GitHub calls for reviewing a pull request: read the PR, post a review, manage its own threads."""

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from review_loop.review import MARKER

# Request URLs at INFO add nothing for users and clutter CI logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

_REVIEWED = re.compile(r"<!-- review-loop sha=([0-9a-f]{7,40}) -->")


class GitHubError(RuntimeError):
    pass


@dataclass
class Thread:
    id: str
    comment_id: int
    path: str
    line: int
    body: str


THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          comments(first: 1) { nodes { databaseId path line originalLine body } }
        }
      }
    }
  }
}"""

RESOLVE_MUTATION = """
mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id isResolved } } }"""


class GitHub:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            transport=transport,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"could not reach GitHub: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise GitHubError(f"{method} {path} returned {response.status_code}")
        return response

    def pull(self, repo: str, number: int) -> dict[str, Any]:
        data: dict[str, Any] = self._request("GET", f"/repos/{repo}/pulls/{number}").json()
        return data

    def diff(self, repo: str, number: int) -> str:
        headers = {"Accept": "application/vnd.github.diff"}
        return self._request("GET", f"/repos/{repo}/pulls/{number}", headers=headers).text

    def compare(self, repo: str, base: str, head: str) -> str:
        headers = {"Accept": "application/vnd.github.diff"}
        return self._request("GET", f"/repos/{repo}/compare/{base}...{head}", headers=headers).text

    def file(self, repo: str, path: str, ref: str) -> str | None:
        """Content at `ref`, or None when the file doesn't exist there (deleted)."""
        try:
            response = self._request(
                "GET",
                f"/repos/{repo}/contents/{path}",
                params={"ref": ref},
                headers={"Accept": "application/vnd.github.raw"},
            )
        except GitHubError as exc:
            if "returned 404" in str(exc):
                return None
            raise
        return response.text

    def last_reviewed_sha(self, repo: str, number: int) -> str | None:
        reviews = self._request(
            "GET", f"/repos/{repo}/pulls/{number}/reviews", params={"per_page": 100}
        ).json()
        shas = [m.group(1) for r in reviews if (m := _REVIEWED.search(r.get("body") or ""))]
        return shas[-1] if shas else None

    def post_review(
        self, repo: str, number: int, sha: str, body: str, comments: list[dict[str, Any]]
    ) -> None:
        payload = {
            "commit_id": sha,
            "body": f"{body}\n\n<!-- review-loop sha={sha} -->",
            "event": "COMMENT",  # never APPROVE or REQUEST_CHANGES: a person decides the merge
            "comments": comments,
        }
        self._request("POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload)

    def own_open_threads(self, repo: str, number: int) -> list[Thread]:
        """Unresolved threads this tool started. Threads started by people are never returned."""
        owner, name = repo.split("/")
        data = self._request(
            "POST",
            "/graphql",
            json={
                "query": THREADS_QUERY,
                "variables": {"owner": owner, "name": name, "number": number},
            },
        ).json()
        nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        threads = []
        for node in nodes:
            first = (node["comments"]["nodes"] or [None])[0]
            if node["isResolved"] or not first or MARKER not in (first.get("body") or ""):
                continue
            line = first.get("line") or first.get("originalLine") or 1
            threads.append(
                Thread(node["id"], first["databaseId"], first["path"], line, first["body"])
            )
        return threads

    def reply_and_resolve(self, repo: str, number: int, thread: Thread, text: str) -> None:
        self._request(
            "POST",
            f"/repos/{repo}/pulls/{number}/comments/{thread.comment_id}/replies",
            json={"body": f"{text}\n\n{MARKER}"},
        )
        self._request(
            "POST", "/graphql", json={"query": RESOLVE_MUTATION, "variables": {"id": thread.id}}
        )
