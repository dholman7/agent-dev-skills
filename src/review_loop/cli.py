"""`review-loop` command line.

review-loop diff FILE [--root DIR] [--no-verify]      review a local unified diff
review-loop pr --repo OWNER/NAME --pr N [--post]      review (or re-review) a pull request
review-loop eval --bugs BUGS.yaml --target DIR        measure catch rate and false alarms
"""

import argparse
import json
import os
import sys
from pathlib import Path

from review_loop import evaluate, loop
from review_loop.diff import parse
from review_loop.github import GitHub
from review_loop.llm import Claude
from review_loop.review import Review, render, review

CLEAN = Path(__file__).parents[2] / "evals" / "review" / "clean.yaml"


def _print(result: Review) -> None:
    for finding in result.findings:
        print(f"--- {finding.path}:{finding.line}\n{render(finding)}\n")
    for finding, reason in result.dropped:
        print(
            f"(dropped {finding.path}:{finding.line} {finding.subject!r}: {reason})",
            file=sys.stderr,
        )
    if not result.findings:
        print("No comments.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="review-loop", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("diff")
    d.add_argument("file", type=Path)
    d.add_argument(
        "--root", type=Path, default=Path("."), help="repo checkout with the changes applied"
    )
    d.add_argument("--no-verify", action="store_true")
    p = sub.add_parser("pr")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--post", action="store_true", help="post the review; without it, print only")
    p.add_argument("--no-verify", action="store_true")
    e = sub.add_parser("eval")
    e.add_argument("--bugs", type=Path, required=True, help="agent-eval-gate's bugs.yaml")
    e.add_argument("--target", type=Path, required=True, help="vendor-intake-app checkout")
    e.add_argument("--clean", type=Path, default=CLEAN)
    e.add_argument("--out", type=Path, default=Path("review-eval.json"))
    e.add_argument("--no-verify", action="store_true")
    args = parser.parse_args(argv)
    llm = Claude()

    if args.command == "diff":
        text = args.file.read_text()
        files = {
            path: (args.root / path).read_text()
            for path in parse(text)
            if (args.root / path).is_file()
        }
        _print(review(llm, text, files, verify_pass=not args.no_verify))
    elif args.command == "pr":
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise SystemExit("Set GITHUB_TOKEN (pull requests: write to post; read to print).")
        outcome = loop.run(
            llm, GitHub(token), args.repo, args.pr, post=args.post, verify_pass=not args.no_verify
        )
        if outcome.skipped:
            print(outcome.skipped)
        elif outcome.review is not None:
            _print(outcome.review)
            print(loop.summary(outcome))
    else:
        cases = evaluate.load_cases(args.bugs, args.clean, args.target)
        report = evaluate.run(llm, cases, verify_pass=not args.no_verify)
        args.out.write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    print(
        f"cost ${llm.spend.cost_usd:.4f} ({', '.join(sorted(llm.spend.models)) or llm.model})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
