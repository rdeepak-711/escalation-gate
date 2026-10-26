"""Board tests for the sweep view: links on both sides, the best PR per issue, and its confidence."""
import gate.board as bd


def test_urls():
    assert bd.issue_url("o/r", 5) == "https://github.com/o/r/issues/5"
    assert bd.pr_url("o/r", 9) == "https://github.com/o/r/pull/9"


def v(pr, verdict, merge="clean", draft=False, checked=(True,), why="because"):
    return {"pr": pr, "verdict": verdict, "merge": merge, "draft": draft, "why": why, "title": f"PR {pr}",
            "fix_lines": [{"file": "a.py", "line": 3, "quote": "x = 1", "checked": c} for c in checked]}


def test_best_pr_is_the_recommended_clean_fix():
    best = bd.best_pr("o/r", "verified", [v(4, "fixes", "conflicts"), v(7, "fixes"), v(2, "partly")])
    assert best["pr"] == 7
    assert best["url"] == "https://github.com/o/r/pull/7"
    assert best["verdict"] == "fixes"
    assert best["why"] == "because"
    assert best["confidence"]["text"] == "high: cause lines checked · fix lines checked · merges clean"


def test_best_pr_falls_back_to_a_fix_that_conflicts_then_to_partly():
    assert bd.best_pr("o/r", "verified", [v(4, "fixes", "conflicts"), v(2, "partly")])["pr"] == 4
    assert bd.best_pr("o/r", "verified", [v(6, "partly"), v(2, "partly"), v(1, "no")])["pr"] == 2


def test_best_pr_never_picks_a_draft_as_recommended_but_still_shows_it():
    best = bd.best_pr("o/r", "verified", [v(4, "fixes", draft=True)])
    assert best["pr"] == 4
    assert best["confidence"]["level"] == "medium"


def test_no_best_pr_when_nothing_fixes():
    assert bd.best_pr("o/r", "verified", [v(1, "no")]) is None
    assert bd.best_pr("o/r", "verified", []) is None


def row(n, status, best=None, res="", inv=None):
    return {"number": n, "title": f"issue {n} <b>", "kind": "bug", "status": status, "inv": inv, "result": res,
            "url": bd.issue_url("o/r", n), "best": best}


def test_board_page_links_issue_and_pr_and_shows_confidence_and_why():
    best = bd.best_pr("o/r", "verified", [v(425, "fixes", why="clamps the total to the role's bounds")])
    html = bd.render_html("o/r", [row(424, "verified", best, "merge #425", inv=3), row(12, None)])
    assert 'href="https://github.com/o/r/issues/424"' in html
    assert 'href="https://github.com/o/r/pull/425"' in html
    assert "high: cause lines checked · fix lines checked · merges clean" in html
    assert "clamps the total to the role&#39;s bounds" in html or "clamps the total to the role's bounds" in html
    assert 'href="issues/424.html"' in html
    assert "issue 12 &lt;b&gt;" in html


def test_board_page_without_a_best_pr_keeps_the_result_text():
    html = bd.render_html("o/r", [row(8, "verified", None, "no open PR fixes it", inv=2)])
    assert "no open PR fixes it" in html
    assert "/pull/" not in html


def test_other_prs_are_every_fix_or_partial_fix_except_the_best_in_verdict_order():
    verdicts = [v(9, "partly"), v(7, "fixes"), v(4, "fixes", "conflicts"), v(2, "no")]
    best = bd.best_pr("o/r", "verified", verdicts)
    others = bd.other_prs("o/r", "verified", verdicts, best)
    assert best["pr"] == 7
    assert [(o["pr"], o["verdict"], o["merge"]) for o in others] == [(4, "fixes", "conflicts"), (9, "partly", "clean")]
    assert others[0]["url"] == "https://github.com/o/r/pull/4"
    assert others[0]["confidence"]["level"] == "medium"


def test_no_other_prs_without_a_best():
    assert bd.other_prs("o/r", "verified", [v(1, "no")], None) == []


def test_board_page_names_the_other_fixing_prs():
    verdicts = [v(425, "fixes"), v(430, "fixes", "conflicts")]
    best = bd.best_pr("o/r", "verified", verdicts)
    r = row(424, "verified", best, "merge #425", inv=3)
    r["others"] = bd.other_prs("o/r", "verified", verdicts, best)
    html = bd.render_html("o/r", [r])
    assert 'href="https://github.com/o/r/pull/430"' in html
    assert "also fixes" in html
