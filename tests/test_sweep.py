"""Sweep tests: rank the open issues, pick which ones a sweep does, and run the loop with every stage faked."""
import pytest

import gate.sweep as sw


# --- 1a. mentions -------------------------------------------------------------------------------------------

def test_mentions_hash_number():
    assert sw.mentions("Fixes #12", 12)
    assert sw.mentions("closes #12.", 12)
    assert sw.mentions("(#12)", 12)


def test_mentions_does_not_match_a_longer_number():
    assert not sw.mentions("Fixes #123", 12)
    assert not sw.mentions("see #1", 12)


def test_mentions_issue_url():
    assert sw.mentions("https://github.com/o/r/issues/12 is the bug", 12)
    assert not sw.mentions("https://github.com/o/r/issues/120", 12)


def test_mentions_empty_text():
    assert not sw.mentions("", 12)
    assert not sw.mentions(None, 12)


# --- 1a. rank_issues ----------------------------------------------------------------------------------------

def issue(n, title="Bug: it breaks", labels=(), thumbs=0, comments=0):
    return {"number": n, "title": title, "labels": list(labels), "thumbs_up": thumbs, "comments": comments}


def pr(n, title="", body=""):
    return {"number": n, "title": title, "body": body}


def test_rank_puts_issues_with_more_linked_prs_first():
    issues = [issue(1), issue(2), issue(3)]
    prs = [pr(10, body="fixes #2"), pr(11, title="Fix #2 again"), pr(12, body="closes #3")]
    ranked = sw.rank_issues(issues, prs)
    assert [r["number"] for r in ranked] == [2, 3, 1]
    assert ranked[0]["linked_prs"] == [10, 11]
    assert ranked[2]["linked_prs"] == []


def test_rank_breaks_ties_by_engagement_then_lowest_number():
    issues = [issue(5, thumbs=1), issue(4, comments=3), issue(3, thumbs=1, comments=2), issue(2)]
    ranked = sw.rank_issues(issues, [])
    assert [r["number"] for r in ranked] == [3, 4, 5, 2]
    assert ranked[0]["engagement"] == 3


def test_rank_puts_features_last_even_with_linked_prs():
    issues = [issue(1, title="Feature: dark mode"), issue(2)]
    ranked = sw.rank_issues(issues, [pr(10, body="#1"), pr(11, body="#1")])
    assert [r["number"] for r in ranked] == [2, 1]
    assert ranked[1]["kind"] == "feature"


def test_rank_keeps_title():
    assert sw.rank_issues([issue(7, title="Bug: x")], [])[0]["title"] == "Bug: x"


# --- 1a. select ---------------------------------------------------------------------------------------------

def ranked(*numbers, features=()):
    return [{"number": n, "kind": "feature" if n in features else "bug"} for n in numbers]


def test_select_top_takes_the_first_n_non_features():
    assert sw.select(ranked(4, 3, 9, 1, features=(3,)), set(), top=2) == [4, 9]


def test_select_top_counts_only_issues_not_done_yet():
    order = ranked(1, 2, 3, 4, 5)
    assert sw.select(order, {1, 2}, top=2) == [3, 4]
    assert sw.select(order, {1, 3}, top=2) == [2, 4]
    assert sw.select(order, {1, 2, 3, 4, 5}, top=2) == []


def test_select_top_skips_done_features_too_and_keeps_rank_order():
    order = ranked(9, 4, 3, 1, 7, features=(3,))
    assert sw.select(order, {9}, top=3) == [4, 1, 7]


def test_select_defaults_to_ten():
    assert sw.select(ranked(*range(1, 20)), set()) == list(range(1, 11))


def test_select_named_issues_keeps_order_and_drops_done_unknown_and_duplicates():
    order = ranked(1, 2, 3, 4, features=(4,))
    assert sw.select(order, {2}, issues=[4, 2, 99, 3, 4]) == [4, 3]


def test_select_named_issues_win_over_top():
    assert sw.select(ranked(1, 2, 3), set(), top=1, issues=[3]) == [3]


# --- 2d. the loop (every stage faked) ------------------------------------------------------------------------

@pytest.fixture
def loop(monkeypatch):
    calls = []
    outcomes = {}
    stop_after = {"n": None}

    def process(conn, repo, issue, model):
        calls.append(("process", issue))
        out = outcomes.get(issue, "verified")
        if isinstance(out, BaseException):
            raise out
        return {"status": out, "cost": 0.5, "error": None}

    def stop_requested(conn, sweep_id):
        done = [c for c in calls if c[0] == "process"]
        return stop_after["n"] is not None and len(done) >= stop_after["n"]

    monkeypatch.setattr(sw, "process_issue", process)
    monkeypatch.setattr(sw, "set_planned", lambda conn, sid, planned: calls.append(("planned", list(planned))))
    monkeypatch.setattr(sw, "set_current", lambda conn, sid, issue: calls.append(("current", issue)))
    monkeypatch.setattr(sw, "record", lambda conn, sid, issue, outcome: calls.append(("record", issue, outcome["status"])))
    monkeypatch.setattr(sw, "stop_requested", stop_requested)
    monkeypatch.setattr(sw, "finish", lambda conn, sid, status, stop_reason=None: calls.append(("finish", status, stop_reason)))
    monkeypatch.setattr(sw, "spent", lambda conn, sid: 0.5 * len([c for c in calls if c[0] == "process"]))
    monkeypatch.setattr(sw.board, "write", lambda conn, repo: calls.append(("board", repo)))
    return calls, outcomes, stop_after


def test_run_processes_in_order_and_updates_the_board_after_each(loop):
    calls, _, _ = loop
    result = sw.run(Conn(), "o/r", 1, [5, 3], "sonnet")
    assert calls == [("planned", [5, 3]),
                     ("current", 5), ("process", 5), ("record", 5, "verified"), ("board", "o/r"),
                     ("current", 3), ("process", 3), ("record", 3, "verified"), ("board", "o/r"),
                     ("finish", "done", None)]
    assert result == {"status": "done", "stop_reason": None,
                      "finished": [{"issue": 5, "outcome": "verified", "cost": 0.5, "error": None},
                                   {"issue": 3, "outcome": "verified", "cost": 0.5, "error": None}]}


def test_run_records_failures_and_keeps_going(loop):
    calls, outcomes, _ = loop
    outcomes[5] = RuntimeError("claude crashed")
    outcomes[3] = "timeout"
    result = sw.run(Conn(), "o/r", 1, [5, 3, 8], "sonnet")
    assert [c for c in calls if c[0] == "record"] == [("record", 5, "error"), ("record", 3, "timeout"),
                                                      ("record", 8, "verified")]
    assert result["status"] == "done"


def test_run_stops_between_issues_when_asked(loop):
    calls, _, stop_after = loop
    stop_after["n"] = 1
    result = sw.run(Conn(), "o/r", 1, [5, 3, 8], "sonnet")
    assert [c[1] for c in calls if c[0] == "process"] == [5]
    assert calls[-1] == ("finish", "stopped", "requested")
    assert result["status"] == "stopped" and result["stop_reason"] == "requested"


def test_run_with_nothing_selected_just_finishes(loop):
    calls, _, _ = loop
    assert sw.run(Conn(), "o/r", 1, [], "sonnet")["status"] == "done"
    assert calls == [("planned", []), ("finish", "done", None)]


# --- review round 1: a failed issue must not poison the rest of the sweep ------------------------------------

class Conn:
    """Stands in for the Postgres connection; records rollbacks when given the call list."""
    def __init__(self, calls=None):
        self.calls = calls

    def rollback(self):
        if self.calls is not None:
            self.calls.append(("rollback",))


def test_a_failed_issue_rolls_back_before_recording_the_error(loop):
    calls, outcomes, _ = loop
    outcomes[5] = RuntimeError("relation does not exist")
    sw.run(Conn(calls), "o/r", 1, [5, 3], "sonnet")
    i = calls.index(("process", 5))
    assert calls[i + 1:i + 3] == [("rollback",), ("record", 5, "error")]
    assert ("process", 3) in calls


def test_a_successful_issue_does_not_roll_back(loop):
    calls, _, _ = loop
    sw.run(Conn(calls), "o/r", 1, [5], "sonnet")
    assert ("rollback",) not in calls


def test_a_system_exit_in_one_issue_is_recorded_and_the_sweep_goes_on(loop):
    calls, outcomes, _ = loop
    outcomes[5] = SystemExit("issue #5 is not in the database")
    result = sw.run(Conn(), "o/r", 1, [5, 3], "sonnet")
    assert [c for c in calls if c[0] == "record"] == [("record", 5, "error"), ("record", 3, "verified")]
    assert result["status"] == "done"


def test_ctrl_c_is_not_swallowed(loop):
    _, outcomes, _ = loop
    outcomes[5] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        sw.run(Conn(), "o/r", 1, [5, 3], "sonnet")


# --- Tier 2: the money cap, and what a failed issue reports --------------------------------------------------

def test_cost_cap_stops_between_issues(loop):
    calls, _, _ = loop
    result = sw.run(Conn(), "o/r", 1, [5, 3, 8], "sonnet", max_usd=1.0)  # 0.5 per issue, cap reached after 2
    assert [c[1] for c in calls if c[0] == "process"] == [5, 3]
    assert calls[-1] == ("finish", "stopped", "cost cap")
    assert result["status"] == "stopped" and result["stop_reason"] == "cost cap"
    assert len(result["finished"]) == 2


def test_no_cap_never_stops_for_money(loop):
    calls, _, _ = loop
    result = sw.run(Conn(), "o/r", 1, [5, 3, 8], "sonnet", max_usd=None)
    assert result["status"] == "done" and len(result["finished"]) == 3


def test_one_issue_may_overrun_the_cap_because_it_is_checked_before_each_issue(loop):
    calls, _, _ = loop
    result = sw.run(Conn(), "o/r", 1, [5, 3], "sonnet", max_usd=0.2)
    assert [c[1] for c in calls if c[0] == "process"] == [5]
    assert result["stop_reason"] == "cost cap"


def test_a_failed_issue_reports_the_error_text_and_no_cost(loop):
    calls, outcomes, _ = loop
    outcomes[5] = RuntimeError("claude -p exited 1: not logged in\nsecond line")
    result = sw.run(Conn(), "o/r", 1, [5], "sonnet")
    assert result["finished"] == [{"issue": 5, "outcome": "error", "cost": 0,
                                  "error": "RuntimeError: claude -p exited 1: not logged in"}]


def test_a_long_error_is_cut_to_200_characters(loop):
    calls, outcomes, _ = loop
    outcomes[5] = RuntimeError("x" * 500)
    assert len(sw.run(Conn(), "o/r", 1, [5], "sonnet")["finished"][0]["error"]) == 200
