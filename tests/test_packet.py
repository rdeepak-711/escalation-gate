"""Step 06 tests: render the packet from stored results, and the merge check, without Claude or Postgres."""
import subprocess

import gate.packet as pk

EVIDENCE = [
    {"file": "github.py", "line": 239, "quote": "fetch_repo_contributors(username, repo_name)", "why": "wrong owner",
     "checked": True},
    {"file": "github.py", "line": 999, "quote": "made up", "why": "x", "checked": False},
]


def data(verdicts):
    return {
        "id": 7, "repo": "o/r", "commit": "e0ece80", "title": "Issue #5: repos missing", "status": "verified",
        "cost": 0.37, "created": "2026-10-11",
        "report": {"cause": "uses <username> as owner", "repro": ["run it", "see 404"], "evidence": EVIDENCE,
                   "confidence": "high"},
        "verdicts": verdicts,
    }


def v(pr, verdict, merge="clean"):
    return {"pr": pr, "title": f"PR {pr}", "author": "a", "verdict": verdict, "why": "because",
            "reasons": ["changes github.py:239"], "merge": merge}


def test_verdicts_are_ordered_fixes_partly_no():
    ordered = pk.order([v(3, "no"), v(2, "partly"), v(9, "fixes"), v(1, "fixes")])
    assert [x["pr"] for x in ordered] == [1, 9, 2, 3]


def test_recommendation_is_the_first_clean_fix():
    assert pk.recommend([v(1, "fixes", "conflicts"), v(2, "fixes", "clean"), v(3, "partly")])["pr"] == 2


def test_no_recommendation_without_a_fix():
    assert pk.recommend([v(3, "partly"), v(4, "no")]) is None


def test_markdown_has_every_section_and_honest_labels():
    md = pk.render_markdown(data([v(425, "fixes"), v(462, "partly"), v(11, "no")]))
    for heading in ("## Summary", "## How to reproduce", "## Root cause", "## Open PRs checked against this cause",
                    "## Limits of this packet"):
        assert heading in md
    assert "checked against the code" in md
    assert "judged from the diff, not run" in md
    assert "#425" in md and "merges clean" in md


def test_a_failed_citation_is_shown_not_hidden():
    md = pk.render_markdown(data([]))
    assert "github.py:999" in md and "❌" in md


def test_competing_prs_are_listed_together():
    md = pk.render_markdown(data([v(425, "fixes"), v(415, "fixes", "conflicts"), v(242, "partly")]))
    assert "#415, #425 and #242 all change the cause" in md


def test_html_escapes_text_from_the_report():
    html = pk.render_html(data([]))
    assert "uses &lt;username&gt; as owner" in html and "<username>" not in html


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_merge_status_clean_and_conflicts(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "clean")
    (tmp_path / "b.txt").write_text("new file\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "clean")
    git(tmp_path, "checkout", "-q", "main")
    git(tmp_path, "checkout", "-qb", "clash")
    (tmp_path / "a.txt").write_text("two\n")
    git(tmp_path, "commit", "-qam", "clash")
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "a.txt").write_text("three\n")
    git(tmp_path, "commit", "-qam", "main moves")

    assert pk.merge_status(tmp_path, "clean") == "clean"
    assert pk.merge_status(tmp_path, "clash") == "conflicts"
    assert pk.merge_status(tmp_path, "no-such-ref") == "unknown"
