"""Confidence labels: built only from what was checked, never a percentage."""
import gate.packet as pk


def v(verdict="fixes", merge="clean", draft=False, checked=(True,), effects=()):
    """effects: None = the side-effect pass did not run; a tuple of checked flags = what it reported."""
    return {"verdict": verdict, "merge": merge, "draft": draft,
            "fix_lines": [{"file": "a.py", "line": i, "quote": "x = 1", "checked": c} for i, c in enumerate(checked)],
            "side_effects": None if effects is None else
            [{"pr": 1, "kind": "removed_name", "file": "a.py", "line": 9, "side": "removed", "quote": "def old",
              "why": "w", "checked": c} for c in effects]}


def test_everything_checked_is_high():
    c = pk.confidence("verified", v())
    assert c["level"] == "high"
    assert c["checks"] == ["cause lines checked", "fix lines checked", "merges clean", "no side effects found"]
    assert c["text"] == "high: cause lines checked · fix lines checked · merges clean · no side effects found"


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


# --- Tier 3: side effects are the fourth check ----------------------------------------------------------------

def test_a_checked_side_effect_caps_at_medium_and_counts():
    c = pk.confidence("verified", v(effects=(True,)))
    assert c["level"] == "medium"
    assert c["checks"][3] == "1 side effect found"
    assert pk.confidence("verified", v(effects=(True, True, False)))["checks"][3] == "2 side effects found"


def test_an_unchecked_side_effect_claim_does_not_count():
    c = pk.confidence("verified", v(effects=(False,)))
    assert c["level"] == "high"
    assert c["checks"][3] == "no side effects found"


def test_side_effects_not_checked_is_never_high():
    c = pk.confidence("verified", v(effects=None))
    assert c["level"] == "medium"
    assert c["checks"][3] == "side effects not checked"
    assert pk.confidence("verified", {**v(), "side_effects": None})["level"] == "medium"


def test_a_verdict_without_the_key_counts_as_not_checked():
    old = v(); del old["side_effects"]
    assert pk.confidence("verified", old)["checks"][3] == "side effects not checked"


def test_draft_still_comes_last():
    assert pk.confidence("verified", v(draft=True, effects=(True,)))["checks"] == [
        "cause lines checked", "fix lines checked", "merges clean", "1 side effect found", "draft PR"]
