"""Packet tests for the PR side: the lines that fix the cause, each marked checked or not, plus the confidence."""
import gate.packet as pk

EVIDENCE = [{"file": "score.py", "line": 40, "quote": "total = base", "why": "no clamp", "checked": True}]


def data(verdicts, status="verified"):
    return {
        "id": 7, "repo": "o/r", "commit": "e0ece80", "title": "Issue #424: score not clamped", "status": status,
        "cost": 0.37, "created": "2026-10-11",
        "report": {"cause": "the total is never clamped", "repro": ["run it"], "evidence": EVIDENCE,
                   "confidence": "high"},
        "verdicts": verdicts,
    }


def v(pr, verdict, lines=(), merge="clean"):
    return {"pr": pr, "title": f"PR {pr}", "author": "a", "verdict": verdict, "why": "because",
            "reasons": ["changes score.py:40"], "merge": merge, "draft": False, "files": ["score.py"],
            "fix_lines": list(lines)}


GOOD = {"file": "score.py", "line": 41, "quote": "total = max(min(base, hi), lo)", "checked": True}
BAD = {"file": "score.py", "line": 90, "quote": "made up", "checked": False}


def test_html_shows_the_lines_that_fix_it_with_marks():
    html = pk.render_html(data([v(425, "fixes", [GOOD, BAD])]))
    assert "Lines that fix it" in html
    assert "score.py:41" in html and "total = max(min(base, hi), lo)" in html
    assert "score.py:90" in html
    assert "✅" in html and "❌" in html


def test_html_links_the_pr_and_shows_confidence():
    html = pk.render_html(data([v(425, "fixes", [GOOD])]))
    assert 'href="https://github.com/o/r/pull/425"' in html
    assert "high: cause lines checked · fix lines checked · merges clean" in html


def test_a_fix_without_lines_says_so():
    html = pk.render_html(data([v(425, "partly", [])]))
    assert "no fix lines given" in html


def test_markdown_also_lists_fix_lines():
    md = pk.render_markdown(data([v(425, "fixes", [GOOD])]))
    assert "score.py:41" in md
    assert "high: cause lines checked" in md


def test_context_adds_confidence_per_verdict():
    ctx = pk.context(data([v(425, "fixes", [GOOD]), v(11, "no")], status="needs_human"))
    by_pr = {x["pr"]: x for x in ctx["verdicts"]}
    assert by_pr[425]["confidence"]["level"] == "low"
    assert by_pr[11]["confidence"] is None
