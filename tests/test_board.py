"""Board tests: classify issues, summarise each one's latest result, render the terminal and HTML views."""
import gate.board as bd


def test_kind_from_title_prefix_and_labels():
    assert bd.kind("Bug: role.json min_final_score is ignored", []) == "bug"
    assert bd.kind("[Bug] Variance in Resume Scores", []) == "bug"
    assert bd.kind("UnicodeEncodeError and JSONDecodeError in score.py", []) == "bug"
    assert bd.kind("Feature: score cover letters", []) == "feature"
    assert bd.kind("Add a Bedrock provider", []) == "feature"
    assert bd.kind("Docs: typo in README", []) == "other"
    assert bd.kind("Docs: typo in README", ["bug"]) == "bug"


def test_status_symbols():
    assert bd.SYMBOL[None] == "⬜" and bd.SYMBOL["verified"] == "✅" and bd.SYMBOL["needs_human"] == "⚠️"


def v(pr, verdict, merge="clean"):
    return {"pr": pr, "verdict": verdict, "merge": merge}


def test_result_prefers_a_clean_fix():
    assert bd.result([v(415, "fixes", "conflicts"), v(425, "fixes"), v(242, "partly")]) == "merge #425"


def test_result_without_a_clean_fix_lists_what_exists():
    assert bd.result([v(415, "fixes", "conflicts"), v(443, "partly")]) == "fixes, conflicts: #415 · partly: #443"
    assert bd.result([v(11, "no")]) == "no open PR fixes it"
    assert bd.result([]) == "no open PR fixes it"


def row(n, status, kind="bug", inv=None, res=""):
    return {"number": n, "title": f"issue {n} <b>", "kind": kind, "status": status, "inv": inv, "result": res}


def test_counts():
    rows = [row(1, "verified", inv=3, res="merge #5"), row(2, None), row(3, None, "feature"), row(4, "needs_human", inv=4)]
    assert bd.counts(rows) == {"open": 4, "bugs": 3, "done": 2, "ready": 1}


def test_terminal_table_has_symbols_and_totals():
    text = bd.render_table([row(424, "verified", inv=6, res="merge #425"), row(476, None)])
    assert "✅" in text and "⬜" in text and "merge #425" in text
    assert "2 open issues · 2 bugs · 1 investigated · 1 ready to merge" in text


def test_html_links_done_issues_to_their_packet_and_escapes_titles():
    html = bd.render_html("o/r", [row(424, "verified", inv=6, res="merge #425"), row(476, None)], open_prs=192)
    assert 'href="issues/424.html"' in html
    assert 'href="issues/476.html"' not in html
    assert "issue 424 &lt;b&gt;" in html and "<b>" not in html.split("<body>")[1].split("issue 424")[1][:5]
    assert "192" in html


def test_a_draft_fix_is_not_shown_as_merge():
    from gate.board import result
    assert result([{"pr": 4952, "verdict": "fixes", "merge": "clean", "draft": True}]) == "fixes, draft: #4952"
    assert result([{"pr": 1, "verdict": "fixes", "merge": "clean", "draft": True},
                   {"pr": 2, "verdict": "fixes", "merge": "clean", "draft": False}]) == "merge #2"
