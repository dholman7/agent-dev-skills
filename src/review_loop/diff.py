"""Unified diffs: parse what changed, and build diffs from before/after text."""

import difflib
import re
from dataclasses import dataclass, field

# @@ -old_start[,old_count] +new_start[,new_count] @@; an omitted count is 1.
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


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
    # Lines still to come in the current hunk. Inside a hunk every line is content, so an added
    # line that happens to read "++ x" (raw: "+++ x") isn't mistaken for a file header.
    old_left = new_left = 0
    for line in diff.splitlines():
        in_hunk = old_left > 0 or new_left > 0
        if not in_hunk and line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            current = None if path == "/dev/null" else files.setdefault(path, FileDiff(path))
            continue
        if current is None or (not in_hunk and line.startswith(("--- ", "diff --git"))):
            continue
        current.text += line + "\n"
        match = None if in_hunk else _HUNK.match(line)
        if match:
            old_count, new_start, new_count = match.groups()
            new_line = int(new_start)
            old_left = int(old_count) if old_count is not None else 1
            new_left = int(new_count) if new_count is not None else 1
            continue
        if line.startswith("+"):
            current.added.add(new_line)
            current.commentable.add(new_line)
            new_line += 1
            new_left -= 1
        elif line.startswith(" "):
            current.commentable.add(new_line)
            new_line += 1
            old_left -= 1
            new_left -= 1
        elif line.startswith("-"):
            # Exists only on the old side; remember the neighbors it sat between.
            current.deleted_near.update({max(new_line - 1, 1), new_line})
            old_left -= 1
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
