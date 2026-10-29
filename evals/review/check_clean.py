"""Proves each harmless change is harmless: applied alone to a copy of vendor-intake-app, the
backend's lint, format, types, tests, and OpenAPI document must all come out unchanged.

    uv run python evals/review/check_clean.py evals/review/clean-hard.yaml \
        --target ../vendor-intake-app
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

CHECKS = [["make", "lint", "typecheck", "test"], ["make", "openapi"]]
# Must FAIL, or the checks aren't seeing the modified copy: exactly 10 MiB is a legal upload.
CONTROL = {
    "id": "control-reject-exactly-10-mib",
    "file": "backend/src/vendor_intake/services/documents.py",
    "old": "    if size > max_bytes:",
    "new": "    if size >= max_bytes:",
}


def check(target: Path, change: dict[str, str]) -> str | None:
    """None when every check passes; otherwise the first failure's output tail."""
    with tempfile.TemporaryDirectory(prefix="clean-check-") as tmp:
        copy = Path(tmp) / "app"
        shutil.copytree(
            target,
            copy,
            symlinks=True,
            ignore=shutil.ignore_patterns("node_modules", ".venv", "frontend", "e2e", "data"),
        )
        (copy / "backend" / ".venv").symlink_to((target / "backend" / ".venv").resolve())
        path = copy / change["file"]
        source = path.read_text()
        if source.count(change["old"]) != 1:
            return "`old` doesn't match exactly once"
        path.write_text(source.replace(change["old"], change["new"]))
        # The copy shares the real checkout's virtualenv. Never let uv re-sync it (that would
        # re-point its editable install at this temporary copy); put the copy's code first instead.
        env = {**os.environ, "UV_NO_SYNC": "1", "PYTHONPATH": str(copy / "backend" / "src")}
        env.pop("VIRTUAL_ENV", None)
        for command in CHECKS:
            result = subprocess.run(
                command, cwd=copy, env=env, capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                return f"{' '.join(command)}: {(result.stdout + result.stderr).strip()[-600:]}"
        if (copy / "openapi.json").read_text() != (target / "openapi.json").read_text():
            return "the OpenAPI document changed"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("changes", type=Path)
    parser.add_argument("--target", type=Path, required=True, help="vendor-intake-app checkout")
    args = parser.parse_args()
    changes = yaml.safe_load(args.changes.read_text())["changes"]
    control = check(args.target, CONTROL)
    if control is None:
        print(f"FAIL {CONTROL['id']}: a breaking change passed, so these checks prove nothing")
        return 1
    reason = next((line for line in control.splitlines() if line.startswith("FAILED")), control)
    print(f"ok   {CONTROL['id']} fails as it should: {reason[:120]}")
    failures = 0
    for change in changes:
        problem = check(args.target, change)
        print(f"{'ok  ' if problem is None else 'FAIL'} {change['id']}")
        if problem:
            failures += 1
            print(f"     {problem}")
    print(f"{len(changes) - failures}/{len(changes)} harmless")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
