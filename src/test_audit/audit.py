"""Audit a repo's Playwright tests against a written standard, or blind, with every finding
checked against the code before it is reported."""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from review_loop.llm import LLM
from review_loop.review import excerpt
from review_loop.review import locate as locate_evidence

STANDARD = (
    Path(__file__).parents[2]
    / "plugins"
    / "agent-dev-skills"
    / "skills"
    / "playwright-testing"
    / "SKILL.md"
)
# Files that say how tests are written; everything else under the test dir must be code.
GUIDES = ("AGENTS.md", "CLAUDE.md")
INCLUDE = re.compile(r"\.(ts|mts|cts|js|mjs|cjs)$|(^|/)(package\.json|AGENTS\.md|CLAUDE\.md)$")
EXCLUDE = re.compile(
    r"(^|/)(node_modules|playwright-report|test-results|blob-report)/|package-lock\.json$"
)
MAX_FILE_BYTES = 60_000


class Finding(BaseModel):
    rule: str
    path: str
    line: int
    evidence: str
    problem: str
    fix: str
    # For a gap no rule covers: the rule to add, and how a tool could detect it.
    proposed_rule: str = ""


@dataclass
class Audit:
    findings: list[Finding]
    dropped: list[tuple[Finding, str]] = field(default_factory=list)
    adjusted: list[str] = field(default_factory=list)


def read_tree(repo: Path, ref: str | None, test_dir: str) -> dict[str, str]:
    """Test files at `ref` (or the working tree), by repo-relative path."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        ).stdout

    if ref is None:
        paths = [str(p.relative_to(repo)) for p in (repo / test_dir).rglob("*") if p.is_file()]
    else:
        paths = git("ls-tree", "-r", "--name-only", ref, "--", test_dir).split()

    def read(path: str) -> str:
        return (
            (repo / path).read_text(errors="replace")
            if ref is None
            else git("show", f"{ref}:{path}")
        )

    files = {}
    for path in sorted(paths):
        if INCLUDE.search(path) and not EXCLUDE.search(path):
            text = read(path)
            if len(text.encode()) <= MAX_FILE_BYTES:
                files[path] = text
    return files


def standard_text(path: Path = STANDARD) -> str:
    """The skill's rules without its provenance, which names the gaps they came from."""
    text = path.read_text()
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    return re.split(r"\n## Provenance\b", text)[0].strip()


SYSTEM_STANDARD = """You audit a repository's Playwright tests against the team's testing standard.

Report every place the tests break a rule, one finding per occurrence: if three spec files repeat
the same setup, that is three findings. Also report test-quality problems the standard has no rule
for, with rule "new" and a proposed_rule: one sentence stating the rule and how a tool could detect
it (a lint rule, a pattern), or "review only" if nothing can.

For each finding:
- rule: the standard's rule ID, such as PW-4, or "new".
- path and line: where the problem is. For a problem with the repo as a whole (a missing lint
  setup, say), cite the most relevant file and line, such as package.json.
- evidence: the exact line of code, copied character for character.
- problem: what is wrong and what it costs, in one or two sentences.
- fix: the change that resolves it, concretely.

Report only what the code shows. A test that already follows a rule is not a finding. An empty list
is a good audit of a good suite."""

SYSTEM_BLIND = """You audit a repository's Playwright tests for test-quality problems, using your
own knowledge of Playwright and test-automation best practice. There is no written standard.

Report every place a practice is broken, one finding per occurrence: if three spec files repeat the
same setup, that is three findings. Look at structure (fixtures, shared setup, page objects),
reliability (waits, flakiness, data isolation), assertions (what they check, how failures read),
security (credentials in code), and enforcement (linting, CI).

For each finding:
- rule: a short kebab-case name for the practice, such as no-hardcoded-credentials.
- path and line: where the problem is. For a problem with the repo as a whole, cite the most
  relevant file and line, such as package.json.
- evidence: the exact line of code, copied character for character.
- problem: what is wrong and what it costs, in one or two sentences.
- fix: the change that resolves it, concretely.
- proposed_rule: one sentence a team could adopt as a rule, and how a tool could detect it.

Report only what the code shows. A test that already follows good practice is not a finding."""

FINDING_PROPERTIES: dict[str, Any] = {
    "rule": {"type": "string"},
    "path": {"type": "string"},
    "line": {"type": "integer"},
    "evidence": {"type": "string"},
    "problem": {"type": "string"},
    "fix": {"type": "string"},
    "proposed_rule": {"type": "string"},
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": FINDING_PROPERTIES,
                "required": list(FINDING_PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

VERIFY_SYSTEM = """You check findings from a test-quality audit before anyone acts on them. For each
finding you get the claim and the code around the cited line. Confirm it only if the code shows the
problem as described. Drop it if the code doesn't show it, if the cited code already does the right
thing, or if it is a matter of taste with no cost to readers, reliability, or security."""

# With a standard, its rules are not matters of taste: the team adopted them.
VERIFY_SYSTEM_STANDARD = """You check findings from a test-quality audit before anyone acts on them.
The audit applies the team's testing standard, given below. For each finding you get the claim and
the code around the cited line.

- A finding that cites a rule ID: confirm it if the code breaks that rule as the standard states it,
  even when the cost looks small, because the team adopted the rule. Drop it if the code follows the
  rule, or if the code shown doesn't break it.
- A finding with rule "new": confirm it only if the code shows a real cost to readers, reliability,
  or security that no rule covers. Drop matters of taste."""
VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["confirm", "drop"]},
                    "reason": {"type": "string"},
                },
                "required": ["index", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def numbered(files: dict[str, str]) -> str:
    blocks = []
    for path, text in files.items():
        body = "\n".join(f"{n}: {line}" for n, line in enumerate(text.splitlines(), 1))
        blocks.append(f'<file path="{path}">\n{body}\n</file>')
    return "\n\n".join(blocks)


def check_citations(findings: list[Finding], files: dict[str, str]) -> Audit:
    """Deterministic: the file must exist and the quoted code must be in it."""
    result = Audit(findings=[])
    for finding in findings:
        content = files.get(finding.path)
        if content is None:
            result.dropped.append((finding, "cites a file that isn't in the audited tree"))
            continue
        actual = locate_evidence(content, finding.line, finding.evidence)
        if actual is None:
            result.dropped.append((finding, "the quoted code isn't in the file"))
            continue
        if actual != finding.line:
            result.adjusted.append(f"{finding.path}: line {finding.line} -> {actual}")
            finding = finding.model_copy(update={"line": actual})
        result.findings.append(finding)
    return result


def verify(llm: LLM, checked: Audit, files: dict[str, str], standard: str | None = None) -> Audit:
    if not checked.findings:
        return checked
    blocks = [
        f'<finding index="{i}">\n{f.rule}: {f.problem}\nFix: {f.fix}\nCited: {f.path}:{f.line}\n'
        f"<code>\n{excerpt(files[f.path], f.line)}\n</code>\n</finding>"
        for i, f in enumerate(checked.findings)
    ]
    system, prompt = VERIFY_SYSTEM, "\n\n".join(blocks)
    if standard is not None:
        system, prompt = VERIFY_SYSTEM_STANDARD, f"<standard>\n{standard}\n</standard>\n\n{prompt}"
    verdicts = {v["index"]: v for v in llm.call(system, prompt, VERIFY_SCHEMA)["verdicts"]}
    result = Audit(findings=[], dropped=list(checked.dropped), adjusted=list(checked.adjusted))
    for i, finding in enumerate(checked.findings):
        verdict = verdicts.get(i)
        if verdict is not None and verdict["verdict"] == "confirm":
            result.findings.append(finding)
        else:
            reason = verdict["reason"] if verdict else "the verifier returned no verdict"
            result.dropped.append((finding, f"verifier: {reason}"))
    return result


def audit(
    llm: LLM, files: dict[str, str], standard: str | None, *, verify_pass: bool = True
) -> Audit:
    """`standard=None` audits blind, from general best practice."""
    guides = "\n\n".join(
        f'<repo_guide path="{p}">\n{t}\n</repo_guide>'
        for p, t in files.items()
        if p.endswith(GUIDES)
    )
    if standard is None:
        system, preamble = SYSTEM_BLIND, ""
    else:
        system, preamble = SYSTEM_STANDARD, f"<standard>\n{standard}\n</standard>\n\n"
    prompt = f"{preamble}{guides}\n\n<tests>\n{numbered(files)}\n</tests>"
    raw = llm.call(system, prompt, SCHEMA)["findings"]
    checked = check_citations([Finding.model_validate(f) for f in raw], files)
    return verify(llm, checked, files, standard) if verify_pass else checked
