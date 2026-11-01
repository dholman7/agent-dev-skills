"""test-audit with a scripted model: tree reading, citation checks, verification, and scoring."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from test_audit import evaluate
from test_audit.audit import Finding, audit, check_citations, read_tree, standard_text
from tests.test_review import Scripted

SPEC = 'import { test } from "@playwright/test";\n\ntest("x", async ({ page }) => {\n  await page.request.post("/api/auth/login", { data: { password: "demo-password" } });\n});\n'


def _finding(**overrides: Any) -> dict[str, Any]:
    base = {
        "rule": "PW-4",
        "path": "e2e/ui/a.spec.ts",
        "line": 4,
        "evidence": 'data: { password: "demo-password" }',
        "problem": "Hardcoded password in a spec.",
        "fix": "Use the signIn fixture.",
        "proposed_rule": "",
    }
    return base | overrides


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "e2e" / "ui").mkdir(parents=True)
    (tmp_path / "e2e" / "ui" / "a.spec.ts").write_text(SPEC)
    (tmp_path / "e2e" / "package.json").write_text("{}\n")
    (tmp_path / "e2e" / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "e2e" / "node_modules" / "x" / "index.js").write_text("ignored")
    (tmp_path / "e2e" / "package-lock.json").write_text("{}")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "e2e/ui", "e2e/package.json"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_read_tree_takes_test_code_and_skips_dependencies_both_from_disk_and_git(
    repo: Path,
) -> None:
    (repo / "e2e" / "ui" / "a.spec.ts").write_text("// changed after the commit\n")

    on_disk = read_tree(repo, None, "e2e")
    at_commit = read_tree(repo, "main", "e2e")

    assert set(on_disk) == set(at_commit) == {"e2e/package.json", "e2e/ui/a.spec.ts"}
    assert "demo-password" in at_commit["e2e/ui/a.spec.ts"]
    assert "changed" in on_disk["e2e/ui/a.spec.ts"]


def test_standard_text_drops_frontmatter_and_provenance(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "---\nname: x\n---\n\n# Rules\n\nPW-1. Do it.\n\n## Provenance\n\nFound in repo y at line 5.\n"
    )

    text = standard_text(skill)

    assert text == "# Rules\n\nPW-1. Do it."


def test_citation_check_drops_unfounded_findings_and_moves_lines_to_the_quote(repo: Path) -> None:
    files = read_tree(repo, "main", "e2e")
    findings = [
        Finding.model_validate(_finding(line=1)),  # quote is on line 4
        Finding.model_validate(_finding(evidence="waitForTimeout(500)")),  # not in the file
        Finding.model_validate(_finding(path="e2e/ui/missing.spec.ts")),
    ]

    result = check_citations(findings, files)

    assert [f.line for f in result.findings] == [4]
    assert [r for _, r in result.dropped] == [
        "the quoted code isn't in the file",
        "cites a file that isn't in the audited tree",
    ]


def test_audit_verifies_and_includes_repo_guides_in_the_prompt(repo: Path) -> None:
    (repo / "e2e" / "AGENTS.md").write_text("Use signIn.\n")
    files = read_tree(repo, None, "e2e")
    llm = Scripted(
        {
            "findings": [
                _finding(),
                _finding(rule="new", line=1, evidence="import { test }", problem="taste"),
            ]
        },
        {
            "verdicts": [
                {"index": 0, "verdict": "confirm", "reason": "visible"},
                {"index": 1, "verdict": "drop", "reason": "taste"},
            ]
        },
    )

    result = audit(llm, files, "PW-4. No credentials.")

    assert [f.rule for f in result.findings] == ["PW-4"]
    assert result.dropped[0][1] == "verifier: taste"
    assert "<standard>\nPW-4. No credentials.\n</standard>" in llm.prompts[0]
    assert '<repo_guide path="e2e/AGENTS.md">' in llm.prompts[0]


def test_blind_audit_sends_no_standard(repo: Path) -> None:
    llm = Scripted({"findings": []})

    audit(llm, read_tree(repo, "main", "e2e"), None)

    assert "<standard>" not in llm.prompts[0]


TRUTH = """
gap_commit: a
clean_commit: b
test_dir: e2e
tolerance: 2
gaps:
  - id: creds
    rule: PW-4
    keywords: [password]
    at:
      - {path: e2e/ui/a.spec.ts, lines: [4, 20]}
  - id: setup
    rule: PW-1
    keywords: [duplicat]
    at:
      - {path: e2e/api/b.spec.ts, range: [5, 40]}
  - id: no-lint
    rule: new
    keywords: [lint]
    any_of: true
    at:
      - {path: e2e/package.json}
      - {path: e2e/playwright.config.ts}
"""


def _f(**overrides: Any) -> Finding:
    return Finding.model_validate(_finding(**overrides))


def test_truth_expands_lines_ranges_and_any_of(tmp_path: Path) -> None:
    (tmp_path / "t.yaml").write_text(TRUTH)

    _, instances = evaluate.load_truth(tmp_path / "t.yaml")

    assert [(i.gap, len(i.places)) for i in instances] == [
        ("creds", 1),
        ("creds", 1),
        ("setup", 1),
        ("no-lint", 2),
    ]


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        (_f(), {"creds"}),  # rule ID, exact line
        (
            _f(rule="no-hardcoded-credentials", line=6),
            {"creds"},
        ),  # blind: keyword, within tolerance
        (_f(line=8), set()),  # outside tolerance
        (_f(rule="PW-9", problem="Bare status check."), set()),  # right place, wrong problem
        (
            _f(rule="PW-1", path="e2e/api/b.spec.ts", line=33, problem="x"),
            {"setup"},
        ),  # inside the range
        (_f(rule="new", path="e2e/package.json", line=1, problem="No lint step."), {"no-lint"}),
        (
            _f(rule="new", path="e2e/package.json", line=1, problem="Outdated dependency."),
            set(),
        ),  # "new" alone names nothing
    ],
)
def test_a_finding_hits_only_the_gap_it_names_where_it_is(
    tmp_path: Path, finding: Finding, expected: set[str]
) -> None:
    (tmp_path / "t.yaml").write_text(TRUTH)
    _, instances = evaluate.load_truth(tmp_path / "t.yaml")

    assert {i.gap for i in instances if evaluate.hits(finding, i)} == expected


def test_score_reports_recall_misses_and_unmatched_findings(tmp_path: Path) -> None:
    (tmp_path / "t.yaml").write_text(TRUTH)
    _, instances = evaluate.load_truth(tmp_path / "t.yaml")
    from test_audit.audit import Audit

    report = evaluate.score(
        Audit(findings=[_f(), _f(rule="PW-7", line=1, problem="CSS locator")]), instances
    )

    assert report["instances_found"]["k"] == 1 and report["instances_found"]["n"] == 4
    assert report["gaps_found"] == {"k": 1, "n": 3}
    assert [u["rule"] for u in report["unmatched"]] == ["PW-7"]
    assert "creds e2e/ui/a.spec.ts" in report["missed"]


def test_score_keeps_every_finding_with_the_gaps_it_hits(tmp_path: Path) -> None:
    (tmp_path / "t.yaml").write_text(TRUTH)
    _, instances = evaluate.load_truth(tmp_path / "t.yaml")
    from test_audit.audit import Audit

    report = evaluate.score(
        Audit(findings=[_f(), _f(rule="PW-7", line=1, problem="CSS locator")]), instances
    )

    assert [(f["rule"], f["hits"]) for f in report["all"]] == [("PW-4", ["creds"]), ("PW-7", [])]


def test_with_a_standard_the_verifier_judges_rules_by_the_standard(repo: Path) -> None:
    llm = Scripted(
        {"findings": [_finding()]},
        {"verdicts": [{"index": 0, "verdict": "confirm", "reason": "breaks PW-4"}]},
    )

    audit(llm, read_tree(repo, "main", "e2e"), "PW-4. No credentials.")

    assert llm.prompts[1].startswith("<standard>\nPW-4. No credentials.\n</standard>")


def test_blind_verifier_gets_no_standard(repo: Path) -> None:
    llm = Scripted(
        {"findings": [_finding(rule="no-hardcoded-credentials")]},
        {"verdicts": [{"index": 0, "verdict": "confirm", "reason": "visible"}]},
    )

    audit(llm, read_tree(repo, "main", "e2e"), None)

    assert "<standard>" not in llm.prompts[1]


def test_truth_reads_all_digit_commit_shas_as_strings(tmp_path: Path) -> None:
    (tmp_path / "t.yaml").write_text(TRUTH.replace("gap_commit: a", "gap_commit: 8315131"))

    truth, _ = evaluate.load_truth(tmp_path / "t.yaml")

    assert truth["gap_commit"] == "8315131"
