"""Unified diffs: parse what changed, and build diffs from before/after text."""

import difflib
import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileDiff:
    path: str
    # New-file line numbers GitHub accepts comments on: added lines and context in hunks.
    commentable: set[int] = field(default_factory=set)
    added: set[int] = field(default_factory=set)
    # New-file lines on either side of a deletion: where a removed line used to be.
    deleted_near: set[int] = field(default_factory=set)
    text: str = ""

    @property
    def changed(self) -> set[int]:
        """Lines a comment about this change could sit on, including pure deletions."""
        return self.added | self.deleted_near


def parse(diff: str) -> dict[str, FileDiff]:
    files: dict[str, FileDiff] = {}
    current: FileDiff | None = None
    new_line = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            current = None if path == "/dev/null" else files.setdefault(path, FileDiff(path))
            continue
        if current is None or line.startswith("--- ") or line.startswith("diff --git"):
            continue
        current.text += line + "\n"
        match = _HUNK.match(line)
        if match:
            new_line = int(match.group(1))
            continue
        if line.startswith("+"):
            current.added.add(new_line)
            current.commentable.add(new_line)
            new_line += 1
        elif line.startswith(" "):
            current.commentable.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            # Exists only on the old side; remember the neighbors it sat between.
            current.deleted_near.update({max(new_line - 1, 1), new_line})
    return files


def make(path: str, before: str, after: str, context: int = 3) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context,
    )
    return "".join(lines)


def nearest(lines: set[int], target: int) -> int | None:
    return min(lines, key=lambda n: (abs(n - target), n)) if lines else None
