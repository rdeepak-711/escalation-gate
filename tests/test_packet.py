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


def v(pr, verdict, merge="clean", draft=False, files=("github.py",)):
    return {"pr": pr, "title": f"PR {pr}", "author": "a", "verdict": verdict, "why": "because",
            "reasons": ["changes github.py:239"], "merge": merge, "draft": draft, "files": list(files)}


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
    assert "change the same files" in md


def test_draft_fix_is_not_recommended():
    assert pk.recommend([v(1, "fixes", draft=True), v(2, "partly")]) is None
    assert pk.recommend([v(1, "fixes", draft=True), v(3, "fixes")])["pr"] == 3


def test_draft_that_fixes_is_named_as_a_draft():
    d = data([v(4952, "fixes", draft=True)])
    for out in (pk.render_markdown(d), pk.render_html(d)):
        assert "#4952" in out and "draft" in out.lower()
        assert "Recommended: merge #4952" not in out and "Recommended:** merge #4952" not in out


def test_prs_on_different_files_are_not_called_duplicates():
    d = data([v(4948, "partly", files=["ts/Triggers.ts"]), v(4954, "partly", files=["python/triggers.py"])])
    for out in (pk.render_markdown(d), pk.render_html(d)):
        assert "duplicate" not in out
        assert "#4948 and #4954 change different files" in out


def test_overlap_groups_are_found_through_shared_files():
    groups = pk.overlap_groups([v(1, "fixes", files=["a"]), v(2, "partly", files=["a", "b"]),
                                v(3, "partly", files=["c"])])
    assert groups == [[1, 2], [3]]


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


def test_long_cause_is_split_into_a_short_lead_and_the_rest():
    cause = "First sentence here. Second one. Third one is extra. Fourth too."
    assert pk.split_cause(cause, limit=40) == ("First sentence here. Second one.", "Third one is extra. Fourth too.")


def test_short_cause_is_not_split():
    assert pk.split_cause("Only one sentence.") == ("Only one sentence.", "")


def test_html_marks_a_needs_human_run_and_a_failed_citation():
    d = data([])
    d["status"] = "needs_human"
    html = pk.render_html(d)
    assert 'class="pill amber">needs_human' in html
    assert "failed check" in html and "verified in code" in html


def test_evidence_is_grouped_by_file_keeping_numbers():
    d = data([])
    d["report"]["evidence"] = [dict(EVIDENCE[0]), {**EVIDENCE[0], "file": "transform.py", "line": 9},
                               {**EVIDENCE[0], "line": 204}]
    groups = pk.context(d)["files"]
    assert [(g["file"], [e["n"] for e in g["items"]]) for g in groups] == [("github.py", [1, 3]), ("transform.py", [2])]


def test_board_puts_each_pr_in_its_verdict_and_merge_cell():
    board = pk.context(data([v(425, "fixes"), v(415, "fixes", "conflicts"), v(242, "partly"), v(11, "no")]))["board"]
    assert [x["pr"] for x in board["fixes"]["clean"]] == [425]
    assert [x["pr"] for x in board["fixes"]["conflicts"]] == [415]
    assert [x["pr"] for x in board["partly"]["clean"]] == [242]
    assert board["partly"]["conflicts"] == []


def test_chain_steps_carry_their_citation():
    d = data([])
    d["report"]["chain"] = [{"step": "owner is wrong", "evidence": 1}, {"step": "made up", "evidence": 2}]
    chain = pk.context(d)["chain"]
    assert chain[0] == {"step": "owner is wrong", "n": 1, "loc": "github.py:239", "short": "github.py:239", "checked": True}
    assert chain[1]["checked"] is False


def test_funnel_counts():
    d = data([v(425, "fixes"), v(242, "partly"), v(11, "no")])
    d["open_prs"] = 192
    html = pk.render_html(d)
    assert "192" in html and "How it breaks" not in html  # no chain in this report, so no flowchart


def test_flowchart_appears_when_there_is_a_chain():
    d = data([])
    d["report"]["chain"] = [{"step": "owner is wrong", "evidence": 1}]
    assert "How it breaks" in pk.render_html(d)
