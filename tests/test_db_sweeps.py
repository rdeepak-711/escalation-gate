"""The SQL behind sweeps, run against a real throwaway Postgres: the schema, every sweep helper, ranking from stored
rows, the board rows, collect's new columns, and the fix lines candidates.run stores. Only claude and gh are faked."""
import json
import os
import sys
import time
from pathlib import Path

import psycopg
import pytest

import gate.board as bd
import gate.candidates as cd
import gate.collect as col
import gate.db as gdb
import gate.mcp_server as ms
import gate.sweep as sw
from conftest import add_inv, add_issue, add_pr


def status_of(conn, sweep_id):
    return conn.execute("SELECT status FROM sweeps WHERE id = %s", (sweep_id,)).fetchone()[0]


# --- schema -------------------------------------------------------------------------------------------------

def test_connect_twice_keeps_the_schema(db):
    gdb.connect().close()
    assert db.execute("SELECT count(*) FROM sweeps").fetchone()[0] == 0


def test_connect_does_not_wait_behind_another_open_transaction(db):
    """A sweep holds a read transaction open while claude runs for minutes; the MCP server must still connect."""
    add_issue(db, 1)
    blocker = gdb.connect()
    try:
        blocker.execute("SELECT * FROM issues").fetchall()
        blocker.execute("SELECT * FROM pr_verdicts").fetchall()  # now idle in transaction, holding read locks
        started = time.monotonic()
        gdb.connect().close()
        assert time.monotonic() - started < 2
    finally:
        blocker.close()


def test_connect_on_an_older_database_adds_the_new_columns(db):
    db.execute("ALTER TABLE pr_verdicts DROP COLUMN fix_lines")
    db.execute("DROP TABLE sweeps")
    db.commit()
    gdb.connect().close()
    assert db.execute("SELECT fix_lines FROM pr_verdicts LIMIT 0").description[0].name == "fix_lines"
    assert db.execute("SELECT count(*) FROM sweeps").fetchone()[0] == 0


def test_sweep_status_is_constrained(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO sweeps (repo, status) VALUES ('o/r', 'bogus')")
    db.rollback()


# --- creating a sweep is committed at once (the sweep process uses its own connection) ----------------------

def test_sweep_create_is_visible_to_another_connection_at_once(db):
    sid = sw.create(db, "o/r", 3, None, "sonnet")
    other = gdb.connect()
    try:
        row = other.execute("SELECT repo, status, top, issues, model, pid FROM sweeps WHERE id = %s",
                            (sid,)).fetchone()
    finally:
        other.close()
    assert row == ("o/r", "running", 3, None, "sonnet", os.getpid())


def test_mcp_create_sweep_is_visible_to_another_connection_at_once(db):
    sid = ms.create_sweep(db, "o/r", None, [424, 431], "opus")
    other = gdb.connect()
    try:
        row = other.execute("SELECT repo, status, top, issues, model, pid FROM sweeps WHERE id = %s",
                            (sid,)).fetchone()
    finally:
        other.close()
    assert row == ("o/r", "running", None, [424, 431], "opus", None)


# --- the life of one sweep ----------------------------------------------------------------------------------

def test_sweep_round_trip(db):
    sid = ms.create_sweep(db, "o/r", 2, None, "sonnet")
    ms.set_pid(db, sid, 123, Path("/x/sweep-1.log"))
    sw.set_planned(db, sid, [5, 3])
    sw.set_current(db, sid, 5)
    sw.record(db, sid, 5, "verified")
    assert ms.latest_sweep(db, "o/r") == {"id": sid, "status": "running", "pid": 123, "planned": [5, 3],
                                           "current": 5, "finished": [{"issue": 5, "outcome": "verified"}],
                                           "log": "/x/sweep-1.log"}
    sw.record(db, sid, 3, "timeout")
    sw.finish(db, sid, "done")
    latest = ms.latest_sweep(db, "o/r")
    assert latest["status"] == "done" and latest["current"] is None
    assert latest["finished"] == [{"issue": 5, "outcome": "verified"}, {"issue": 3, "outcome": "timeout"}]
    assert db.execute("SELECT finished_at IS NOT NULL FROM sweeps WHERE id = %s", (sid,)).fetchone()[0]


def test_stop_is_asked_and_seen_between_issues(db):
    sid = ms.create_sweep(db, "o/r", 2, None, "sonnet")
    assert not sw.stop_requested(db, sid)
    ms.request_stop(db, sid)
    assert sw.stop_requested(db, sid)
    assert status_of(db, sid) == "stopping"


def test_stop_does_not_reopen_a_finished_sweep(db):
    sid = ms.create_sweep(db, "o/r", 2, None, "sonnet")
    sw.finish(db, sid, "done")
    ms.request_stop(db, sid)
    assert status_of(db, sid) == "done"
    assert not sw.stop_requested(db, sid)


def test_mark_error(db):
    sid = ms.create_sweep(db, "o/r", 2, None, "sonnet")
    sw.set_current(db, sid, 5)
    ms.mark_error(db, sid)
    latest = ms.latest_sweep(db, "o/r")
    assert latest["status"] == "error" and latest["current"] is None


def test_latest_sweep_is_the_newest_for_that_repo(db):
    ms.create_sweep(db, "o/r", 1, None, "sonnet")
    newest = ms.create_sweep(db, "o/r", 2, None, "sonnet")
    ms.create_sweep(db, "o/other", 3, None, "sonnet")
    assert ms.latest_sweep(db, "o/r")["id"] == newest
    assert ms.latest_sweep(db, "nobody/here") is None


# --- planning from stored issues, PRs and investigations ----------------------------------------------------

@pytest.fixture
def seeded(db):
    add_issue(db, 1)
    add_issue(db, 2)
    add_issue(db, 3, title="Feature: dark mode")
    add_issue(db, 4)
    add_issue(db, 5)
    add_issue(db, 6)
    add_issue(db, 7, repo="o/elsewhere")
    add_pr(db, 10, body="fixes #2")
    add_pr(db, 11, title="Fix #2 again")
    for n in (12, 13, 14):
        add_pr(db, n, body="#3")
    add_pr(db, 15, body="closes https://github.com/o/r/issues/4")
    add_inv(db, 4, "verified")             # done
    add_inv(db, 5, "needs_human", hidden=True)  # hidden, so not done
    add_inv(db, 6, "timeout")              # a timeout is not done
    return db


def test_plan_ranks_by_linked_prs_and_skips_done_issues(seeded):
    assert sw.plan(seeded, "o/r", 10, None) == [2, 1, 5, 6]


def test_plan_top_counts_only_issues_not_done_yet(seeded):
    assert sw.plan(seeded, "o/r", 2, None) == [2, 1]


def test_plan_named_issues_keep_their_order(seeded):
    assert sw.plan(seeded, "o/r", None, [3, 4, 99, 6]) == [3, 6]


def test_plan_counts_needs_human_as_done(db):
    add_issue(db, 1)
    add_issue(db, 2)
    add_inv(db, 1, "needs_human")
    assert sw.plan(db, "o/r", 10, None) == [2]


# --- the loop keeps going after a real database error -------------------------------------------------------

def test_run_survives_a_database_error_in_one_issue(db, monkeypatch):
    def process(conn, repo, issue, model):
        if issue == 5:
            conn.execute("SELECT * FROM no_such_table")  # aborts the transaction
        return "verified"
    monkeypatch.setattr(sw, "process_issue", process)
    monkeypatch.setattr(sw.board, "write", lambda conn, repo: None)
    sid = sw.create(db, "o/r", 2, None, "sonnet")
    result = sw.run(db, "o/r", sid, [5, 3], "sonnet")
    assert result == {"status": "done", "finished": [{"issue": 5, "outcome": "error"},
                                                      {"issue": 3, "outcome": "verified"}]}
    latest = ms.latest_sweep(db, "o/r")
    assert latest["status"] == "done"
    assert latest["finished"] == result["finished"]


# --- board rows from the database ---------------------------------------------------------------------------

GOOD_LINE = {"file": "score.py", "line": 41, "quote": "total = max(min(base, hi), lo)", "checked": True}


@pytest.fixture
def judged(db):
    add_issue(db, 1)
    add_issue(db, 2, comments=1)
    add_pr(db, 10, body="fixes #2")
    inv = add_inv(db, 2, "verified", report={"cause": "c", "repro": [], "evidence": [], "confidence": "high"})
    db.execute("INSERT INTO pr_verdicts (investigation_id, pr, reasons, verdict, why, merge, fix_lines) "
               "VALUES (%s, 10, '{}', 'fixes', 'clamps the total', 'clean', %s::jsonb)",
               (inv, json.dumps([GOOD_LINE])))
    db.commit()
    return db, inv


def test_board_rows_follow_the_rank_and_carry_the_best_pr(judged):
    db, inv = judged
    rows = bd.rows(db, "o/r")
    assert [r["number"] for r in rows] == [2, 1]
    best = rows[0]["best"]
    assert best["pr"] == 10 and best["url"] == "https://github.com/o/r/pull/10"
    assert best["why"] == "clamps the total"
    assert best["confidence"]["text"] == "high: cause lines checked · fix lines checked · merges clean"
    assert rows[0]["inv"] == inv and rows[0]["url"] == "https://github.com/o/r/issues/2"
    assert rows[1]["best"] is None and rows[1]["inv"] is None


def test_mcp_tools_read_the_real_database(judged, monkeypatch, tmp_path):
    db, _ = judged
    monkeypatch.setattr(ms, "SITE_DIR", tmp_path / "site")
    assert ms.sweep_status("o/r") == {"status": "none"}
    board = ms.get_board("o/r")
    assert [r["number"] for r in board["rows"]] == [2]
    assert board["rows"][0]["best"]["pr"] == 10
    assert board["rows"][0]["page"] == str(tmp_path / "site" / "r" / "issues" / "2.html")

    sid = ms.create_sweep(db, "o/r", 2, None, "sonnet")
    ms.set_pid(db, sid, os.getpid(), tmp_path / "x.log")  # a live pid
    sw.set_planned(db, sid, [2, 1])
    sw.record(db, sid, 2, "verified")
    status = ms.sweep_status("o/r")
    assert status["status"] == "running" and status["done"] == 1 and status["total"] == 2
    assert status["finished"] == [{"issue": 2, "outcome": "verified", "result": bd.rows(db, "o/r")[0]["result"]}]

    assert ms.stop_sweep("o/r") == {"sweep_id": sid, "status": "stopping"}
    assert status_of(db, sid) == "stopping"


# --- start_sweep with a real child process that dies before finishing ---------------------------------------

def test_a_sweep_process_that_dies_is_marked_error_and_does_not_block_the_next(db, monkeypatch, tmp_path):
    real_popen = ms.subprocess.Popen
    spawned = []

    def dies_at_once(cmd, **kwargs):
        proc = real_popen([sys.executable, "-c", "import sys; sys.exit(3)"], **kwargs)
        spawned.append(proc)  # keep it referenced, as the server does not wait on it
        return proc
    monkeypatch.setattr(ms.subprocess, "Popen", dies_at_once)
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(ms, "SITE_DIR", tmp_path / "site")

    first = ms.start_sweep("o/r", top=2)
    assert first["status"] == "running"
    assert Path(first["log"]).exists()

    deadline = time.monotonic() + 10
    while ms.sweep_status("o/r")["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ms.sweep_status("o/r")["status"] == "error"

    second = ms.start_sweep("o/r", top=2)
    assert second["sweep_id"] != first["sweep_id"]


# --- collect stores thumbs-up and comment counts ------------------------------------------------------------

def fake_gh(issues):
    def gh(*args):
        if args[:2] == ("issue", "list"):
            return json.dumps(issues)
        if args[:2] == ("pr", "list"):
            return "[]"
        return ""
    return gh


def issue_json(n, thumbs=None, comments=0, other=None):
    groups = [{"content": "HEART", "users": {"totalCount": 9}}]
    if thumbs is not None:
        groups.append({"content": "THUMBS_UP", "users": {"totalCount": thumbs}})
    return {"number": n, "title": f"Bug {n}", "body": None, "labels": [{"name": "bug"}],
            "reactionGroups": groups, "comments": [{"body": "x"}] * comments}


def counts(db, n):
    return db.execute("SELECT thumbs_up, comments FROM issues WHERE repo = 'o/r' AND number = %s",
                      (n,)).fetchone()


def test_collect_stores_thumbs_up_and_comments_and_updates_them(db, monkeypatch):
    monkeypatch.setattr(col, "gh", fake_gh([issue_json(1, thumbs=4, comments=3), issue_json(2)]))
    col.collect("o/r")
    assert counts(db, 1) == (4, 3)
    assert counts(db, 2) == (0, 0)
    monkeypatch.setattr(col, "gh", fake_gh([issue_json(1, thumbs=6, comments=5), issue_json(2)]))
    col.collect("o/r")
    assert counts(db, 1) == (6, 5)


# --- candidates.run stores the checked fix lines ------------------------------------------------------------

PATCH = """@@ -10,4 +10,5 @@ def total(scores):
     base = sum(scores)
-    return base
+    capped = min(base, role.max_final_score)
+    return max(capped, role.min_final_score)
     # end"""


def test_candidates_run_stores_checked_fix_lines(db, monkeypatch):
    add_issue(db, 2)
    add_pr(db, 10, body="fixes #2")
    db.execute("INSERT INTO pr_files (repo, pr, filename, status, additions, deletions, patch) "
               "VALUES ('o/r', 10, 'score.py', 'modified', 2, 1, %s)", (PATCH,))
    db.commit()
    inv = add_inv(db, 2, "verified", report={"cause": "c", "repro": [], "confidence": "high",
                                              "evidence": [{"file": "score.py", "line": 11, "quote": "return base",
                                                            "why": "w"}]})
    monkeypatch.setattr(cd, "shortlist", lambda conn, repo, issue, report: [
        {"pr": 10, "reasons": ["changes score.py:11"]}, {"pr": 11, "reasons": ["mentions #2"]}])
    monkeypatch.setattr(cd, "build_prompt", lambda conn, repo, report, candidates: "prompt")
    real = {"file": "score.py", "line": 12, "quote": "return max(capped, role.min_final_score)"}
    made_up = {"file": "score.py", "line": 90, "quote": "made up line"}
    monkeypatch.setattr(cd, "judge", lambda prompt, expected, model: ([
        {"pr": 10, "verdict": "fixes", "why": "clamps", "lines": [real, made_up]},
        {"pr": 11, "verdict": "no", "why": "unrelated", "lines": [real]},
    ], [], [0.1]))
    assert cd.run(db, inv) == []
    stored = dict(db.execute("SELECT pr, fix_lines FROM pr_verdicts WHERE investigation_id = %s", (inv,)))
    assert stored[10] == [{**real, "checked": True}, {**made_up, "checked": False}]
    assert stored[11] == []


def test_board_rows_list_the_other_fixing_prs(judged):
    db, inv = judged
    add_pr(db, 11, body="#2")
    add_pr(db, 12, body="#2")
    add_pr(db, 13, body="#2")
    db.execute("INSERT INTO pr_verdicts (investigation_id, pr, reasons, verdict, why, merge, fix_lines) VALUES "
               "(%s, 12, '{}', 'fixes', 'same fix', 'clean', '[]'), "
               "(%s, 11, '{}', 'partly', 'half', 'conflicts', '[]'), "
               "(%s, 13, '{}', 'no', 'unrelated', 'clean', '[]')", (inv, inv, inv))
    db.commit()
    row = bd.rows(db, "o/r")[0]
    assert row["best"]["pr"] == 10
    assert [(o["pr"], o["verdict"], o["merge"]) for o in row["others"]] == [(12, "fixes", "clean"),
                                                                           (11, "partly", "conflicts")]
    assert row["others"][0]["url"] == "https://github.com/o/r/pull/12"
    assert row["others"][0]["confidence"]["text"].startswith("medium: cause lines checked · no fix lines given")
    assert bd.rows(db, "o/r")[1]["others"] == []


def test_get_board_passes_the_other_fixing_prs_through(judged, monkeypatch, tmp_path):
    db, inv = judged
    add_pr(db, 12, body="#2")
    db.execute("INSERT INTO pr_verdicts (investigation_id, pr, reasons, verdict, why, merge) "
               "VALUES (%s, 12, '{}', 'fixes', 'same fix', 'clean')", (inv,))
    db.commit()
    monkeypatch.setattr(ms, "SITE_DIR", tmp_path / "site")
    row = ms.get_board("o/r")["rows"][0]
    assert [o["pr"] for o in row["others"]] == [12]
