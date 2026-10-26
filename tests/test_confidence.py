"""Confidence labels: built only from what was checked, never a percentage."""
import gate.packet as pk


def v(verdict="fixes", merge="clean", draft=False, checked=(True,)):
    return {"verdict": verdict, "merge": merge, "draft": draft,
            "fix_lines": [{"file": "a.py", "line": i, "quote": "x = 1", "checked": c} for i, c in enumerate(checked)]}


def test_everything_checked_is_high():
    c = pk.confidence("verified", v())
    assert c["level"] == "high"
    assert c["checks"] == ["cause lines checked", "fix lines checked", "merges clean"]
    assert c["text"] == "high: cause lines checked · fix lines checked · merges clean"


def test_no_verdict_has_no_confidence():
    assert pk.confidence("verified", v(verdict="no")) is None


def test_partly_is_never_high():
    assert pk.confidence("verified", v(verdict="partly"))["level"] == "medium"


def test_conflicts_or_unchecked_merge_is_medium():
    assert pk.confidence("verified", v(merge="conflicts"))["checks"][2] == "conflicts"
    assert pk.confidence("verified", v(merge="conflicts"))["level"] == "medium"
    assert pk.confidence("verified", v(merge="unknown"))["checks"][2] == "merge not checked"
    assert pk.confidence("verified", v(merge=None))["checks"][2] == "merge not checked"


def test_draft_is_medium_and_says_so():
    c = pk.confidence("verified", v(draft=True))
    assert c["level"] == "medium"
    assert c["checks"][-1] == "draft PR"


def test_some_fix_lines_unchecked_is_medium_with_a_count():
    c = pk.confidence("verified", v(checked=(True, False, True)))
    assert c["level"] == "medium"
    assert c["checks"][1] == "2 of 3 fix lines checked"


def test_no_fix_line_checked_is_low():
    c = pk.confidence("verified", v(checked=(False, False)))
    assert c["level"] == "low"
    assert c["checks"][1] == "0 of 2 fix lines checked"


def test_no_fix_lines_given_is_medium_not_low():
    c = pk.confidence("verified", v(checked=()))
    assert c["level"] == "medium"
    assert c["checks"][1] == "no fix lines given"


def test_unverified_cause_is_low():
    c = pk.confidence("needs_human", v())
    assert c["level"] == "low"
    assert c["checks"][0] == "some cause lines failed the check"


def test_no_percentages_anywhere():
    for status in ("verified", "needs_human"):
        for verdict in (v(), v(merge="conflicts"), v(checked=())):
            assert "%" not in pk.confidence(status, verdict)["text"]
