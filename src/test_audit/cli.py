"""`test-audit` command line.

test-audit scan --repo PATH [--ref REF] [--test-dir e2e] [--blind]   audit a repo's tests
test-audit eval --repo PATH --truth FILE [--blind] [--out FILE]      score against known gaps

TEST_AUDIT_PROFILE picks the model: dev (Claude Haiku 5.5, default) or report (Claude Opus 5.5).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from review_loop.llm import Claude
from test_audit import evaluate
from test_audit.audit import Audit, audit, read_tree, standard_text


def _print(result: Audit) -> None:
    for f in result.findings:
        print(f"--- {f.rule}  {f.path}:{f.line}\n{f.problem}\nFix: {f.fix}")
        if f.proposed_rule:
            print(f"Proposed rule: {f.proposed_rule}")
        print()
    for f, reason in result.dropped:
        print(f"(dropped {f.path}:{f.line} {f.rule}: {reason})", file=sys.stderr)
    if not result.findings:
        print("No findings.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test-audit", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--repo", type=Path, required=True)
    s.add_argument("--ref", help="git ref to audit; default: the working tree")
    s.add_argument("--test-dir", default="e2e")
    e = sub.add_parser("eval")
    e.add_argument("--repo", type=Path, required=True, help="checkout of the truth file's target")
    e.add_argument("--truth", type=Path, required=True)
    e.add_argument("--out", type=Path, default=Path("audit-eval.json"))
    for p in (s, e):
        p.add_argument(
            "--blind", action="store_true", help="no written standard; general practice only"
        )
        p.add_argument("--no-verify", action="store_true")
    args = parser.parse_args(argv)
    llm = Claude(env_prefix="TEST_AUDIT")
    standard = None if args.blind else standard_text()

    if args.command == "scan":
        files = read_tree(args.repo, args.ref, args.test_dir)
        _print(audit(llm, files, standard, verify_pass=not args.no_verify))
    else:
        truth, instances = evaluate.load_truth(args.truth)
        report: dict[str, object] = {
            "mode": "blind" if args.blind else "standard",
            "verify": not args.no_verify,
        }
        for label, ref in (("gaps", truth["gap_commit"]), ("clean", truth["clean_commit"])):
            files = read_tree(args.repo, ref, truth["test_dir"])
            report[label] = evaluate.score(
                audit(llm, files, standard, verify_pass=not args.no_verify), instances
            )
        report["cost_usd"] = round(llm.spend.cost_usd, 4)
        report["model"] = llm.model
        args.out.write_text(json.dumps(report, indent=2))
        gaps, clean = cast(dict[str, Any], report["gaps"]), cast(dict[str, Any], report["clean"])
        found, kinds = gaps["instances_found"], gaps["gaps_found"]
        print(
            f"{report['mode']}: found {found['k']}/{found['n']} gap instances "
            f"({kinds['k']}/{kinds['n']} kinds); {len(gaps['unmatched'])} other findings; "
            f"{clean['findings']} findings on the fixed suite; ${report['cost_usd']}"
        )
    print(f"cost ${llm.spend.cost_usd:.4f} ({llm.model})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
