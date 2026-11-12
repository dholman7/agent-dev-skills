"""`review-loop` command line.

review-loop diff FILE [--root DIR] [--no-verify]      review a local unified diff
review-loop pr --repo OWNER/NAME --pr N [--post]      review (or re-review) a pull request
review-loop eval --bugs BUGS.yaml --target DIR        measure catch rate and false alarms
review-loop judge RESULTS...                          does each comment name the defect?
review-loop controls RESULTS... --out FILE.json       the same comments vs the wrong defect
review-loop labels RESULTS... --out FILE.md           export judged comments to label by hand
review-loop agreement FILE.md RESULTS...              Cohen's kappa, hand labels vs the judge
review-loop attack --bugs BUGS.yaml --target DIR      does a payload in the PR hide a seeded bug?
"""

import argparse
import json
import os
import sys
from pathlib import Path

from review_loop import attacks, evaluate, judge, loop
from review_loop.diff import parse
from review_loop.github import GitHub
from review_loop.llm import make_llm
from review_loop.review import Review, render, review

CLEAN = Path(__file__).parents[2] / "evals" / "review" / "clean.yaml"
ATTACKS = Path(__file__).parents[2] / "evals" / "review" / "attacks.yaml"


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
    e.add_argument("--bugs", type=Path, help="agent-eval-gate's bugs.yaml; omit to run clean only")
    e.add_argument("--target", type=Path, required=True, help="vendor-intake-app checkout")
    e.add_argument("--clean", type=Path, default=CLEAN)
    e.add_argument("--out", type=Path, default=Path("review-eval.json"))
    e.add_argument("--no-verify", action="store_true")
    j = sub.add_parser("judge", help="add judge verdicts to eval results, in place")
    j.add_argument("results", type=Path, nargs="+")
    c = sub.add_parser("controls", help="build negative controls: comments vs the wrong defect")
    c.add_argument("results", type=Path, nargs="+")
    c.add_argument("--out", type=Path, required=True)
    x = sub.add_parser("labels", help="export judged comments to label (verdicts hidden)")
    x.add_argument("results", type=Path, nargs="+")
    x.add_argument("--out", type=Path, required=True)
    x.add_argument("--sample", type=int, default=100)
    a = sub.add_parser("agreement", help="Cohen's kappa between hand labels and the judge")
    a.add_argument("labels", type=Path)
    a.add_argument("results", type=Path, nargs="+")
    a.add_argument(
        "--exclude", nargs="*", default=[], help="label ids to leave out, e.g. ones seen unblinded"
    )
    t = sub.add_parser("attack", help="prompt injection: does a payload hide a seeded bug?")
    t.add_argument("--bugs", type=Path, required=True, help="agent-eval-gate's bugs.yaml")
    t.add_argument("--target", type=Path, required=True, help="vendor-intake-app checkout")
    t.add_argument("--attacks", type=Path, default=ATTACKS)
    t.add_argument("--case", action="append", help="limit to a case id (repeatable)")
    t.add_argument("--trials", type=int, default=1)
    t.add_argument("--out", type=Path, default=Path("review-attacks.json"))
    t.add_argument("--no-verify", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "controls":
        built = judge.controls(judge.load(args.results))
        args.out.write_text(json.dumps(built, indent=2))
        print(f"wrote {len(built['rows'])} controls to {args.out}")
        return 0
    if args.command == "labels":
        if args.out.exists():
            raise SystemExit(f"{args.out} exists; it may hold labels. Pick another --out.")
        args.out.write_text(judge.export(judge.load(args.results), args.sample))
        print(f"wrote {args.out}")
        return 0
    if args.command == "agreement":
        labels = judge.read_labels(args.labels.read_text())
        unknown = sorted(set(args.exclude) - set(labels))
        if unknown:
            raise SystemExit(f"--exclude names ids not in {args.labels}: {unknown}")
        labels = {k: v for k, v in labels.items() if k not in args.exclude}
        print(json.dumps(judge.agreement(judge.load(args.results), labels), indent=2))
        return 0

    llm = make_llm("REVIEW_JUDGE") if args.command == "judge" else make_llm()

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
    elif args.command == "judge":
        for path in args.results:
            results = judge.judge(llm, json.loads(path.read_text()))
            path.write_text(json.dumps(results, indent=2))
            print(f"{path}: identified {json.dumps(results['bugs_identified'])}")
    elif args.command == "attack":
        chosen = [a for a in attacks.load(args.attacks) if not args.case or a.id in args.case]
        report = attacks.run(
            llm,
            chosen,
            args.bugs,
            args.target,
            trials=args.trials,
            verify_pass=not args.no_verify,
        )
        args.out.write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
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
