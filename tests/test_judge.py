"""The judge pass over saved eval results, the hand-label round trip, and Cohen's kappa."""

from typing import Any

import pytest

from review_loop import judge
from review_loop.diff import make
from tests.test_review import BEFORE, PATH, Scripted, _finding

DIFF = make(PATH, BEFORE, BEFORE.replace("return 201", "return 200"))


def _row(case: str, findings: list[dict[str, Any]], kind: str = "bug") -> dict[str, Any]:
    return {
        "id": case,
        "kind": kind,
        "findings": findings,
        "path": PATH,
        "diff": DIFF,
        "defect": "create answers 200 instead of 201",
    }


def _results() -> dict[str, Any]:
    return {
        "rows": [
            # Two comments near the change: one names the defect, one is about something else.
            _row("b1", [_finding(line=4), _finding(line=4, subject="rename x")]),
            # A comment far from the change, and a non-issue near it: neither is judged.
            _row("b2", [_finding(line=40), _finding(line=4, label="suggestion")]),
            _row("c1", [_finding(line=4)], kind="clean"),
        ]
    }


def test_judge_rules_only_on_located_issues_and_marks_identified_bugs() -> None:
    llm = Scripted(
        {"rationale": "names the 200", "identifies": True},
        {"rationale": "about naming", "identifies": False},
    )
    results = judge.judge(llm, _results())

    assert len(llm.prompts) == 2
    assert "create answers 200 instead of 201" in llm.prompts[0]
    b1, b2, c1 = results["rows"]
    assert b1["identified"] is True and b2["identified"] is False
    assert "identified" not in c1
    assert results["bugs_identified"]["k"] == 1 and results["bugs_identified"]["n"] == 2


def test_judging_again_only_pays_for_new_findings() -> None:
    first = judge.judge(
        Scripted({"rationale": "", "identifies": True}, {"rationale": "", "identifies": False}),
        _results(),
    )
    again = Scripted()
    judge.judge(again, first)
    assert again.prompts == []


def test_export_hides_the_verdict_and_round_trips_with_read_labels() -> None:
    runs = {
        "opus-1": judge.judge(
            Scripted(
                {"rationale": "SECRET", "identifies": True}, {"rationale": "", "identifies": False}
            ),
            _results(),
        )
    }
    text = judge.export(runs, sample=10)
    # Neither the judge's rationale nor its verdict reaches the labeler.
    assert "SECRET" not in text and "True" not in text and "False" not in text
    assert text.count("\nLabel: \n") == 2  # one blank label per judged comment

    labeled = text.replace("Label: \n", "Label: yes\n", 1)
    labels = judge.read_labels(labeled)
    assert list(labels.values()) == [True]
    assert set(labels) <= {judge.label_id(k) for k in judge.pairs(runs)}
    assert "opus-1" not in text  # no run names either


def test_read_labels_refuses_anything_but_yes_or_no() -> None:
    with pytest.raises(ValueError, match="yes or no"):
        judge.read_labels("## run/b1/0\n\nLabel: maybe\n")


@pytest.mark.parametrize(
    ("both_yes", "both_no", "h_yes_j_no", "h_no_j_yes", "kappa"),
    [
        (20, 15, 5, 10, 0.4),  # p_o 0.7, p_e 0.5 (yes rates 0.5 and 0.6)
        (10, 10, 0, 0, 1.0),
        (0, 0, 10, 10, -1.0),
    ],
)
def test_cohen_kappa_matches_hand_computed_values(
    both_yes: int, both_no: int, h_yes_j_no: int, h_no_j_yes: int, kappa: float
) -> None:
    pairs = (
        [(True, True)] * both_yes
        + [(False, False)] * both_no
        + [(True, False)] * h_yes_j_no
        + [(False, True)] * h_no_j_yes
    )
    assert judge.cohen_kappa(pairs) == pytest.approx(kappa)


def test_agreement_rejects_labels_for_findings_it_cannot_find() -> None:
    runs = {
        "r": judge.judge(
            Scripted({"rationale": "", "identifies": True}, {"rationale": "", "identifies": True}),
            _results(),
        )
    }
    with pytest.raises(ValueError, match="not in these results"):
        judge.agreement(runs, {judge.label_id("other/b9/0"): True})
    report = judge.agreement(
        runs, {judge.label_id("r/b1/0"): True, judge.label_id("r/b1/1"): False}
    )
    assert report["both_yes"] == 1 and report["human_no_judge_yes"] == 1


def test_controls_pair_each_comment_with_another_bugs_defect() -> None:
    results = _results()
    results["rows"][1]["defect"] = "duplicate names are accepted"
    built = judge.controls({"r": results})
    assert len(built["rows"]) == 2  # b1's two located comments; b2 has none
    for row in built["rows"]:
        assert row["defect"] == "duplicate names are accepted"
        assert row["diff"] == DIFF and len(row["findings"]) == 1
        assert "judge" not in row["findings"][0]
    assert set(judge.pairs({"controls": built})) == {
        f"controls/{row['id']}/0" for row in built["rows"]
    }


def test_judge_cost_is_charged_per_file() -> None:
    llm = Scripted(*[{"rationale": "", "identifies": True}] * 4)
    llm.spend.cost_usd = 5.0  # spent before this file
    first = judge.judge(llm, _results())
    assert first["judge_cost_usd"] == 0.0


def test_an_export_with_controls_does_not_reveal_them() -> None:
    results = _results()
    results["rows"][1]["defect"] = "duplicate names are accepted"
    text = judge.export({"r": results, "controls": judge.controls({"r": results})}, sample=10)
    assert text.count("\nLabel: \n") == 4
    assert "controls" not in text and "~vs~" not in text
