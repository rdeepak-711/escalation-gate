"""CLI tests for --sweep: argument rules, planning, and what happens when the sweep crashes. Every stage is faked."""
import pytest

import gate.cli as cli
import gate.sweep as sw


class FakeConn:
    def __init__(self, calls=None):
        self.calls = calls if calls is not None else []

    def rollback(self):
        self.calls.append(("rollback",))

    def execute(self, sql, params):
        return self

    def fetchone(self):
        return (12,)  # PRs already collected; a sweep collects again anyway

    def close(self):
        pass


@pytest.fixture
def faked(tmp_path, monkeypatch):
    calls = []
    (tmp_path / "r").mkdir()
    monkeypatch.setattr(cli, "VENDOR", tmp_path)
    conn = FakeConn(calls)
    monkeypatch.setattr(cli, "connect", lambda: conn)
    monkeypatch.setattr(cli, "collect", lambda repo: calls.append(("collect", repo)))
    monkeypatch.setattr(sw, "create", lambda conn, repo, top, issues, model: calls.append(("create", top, issues)) or 9)
    monkeypatch.setattr(sw, "plan", lambda conn, repo, top, issues: calls.append(("plan", top, issues)) or [5, 3])
    monkeypatch.setattr(sw, "run", lambda conn, repo, sid, selected, model:
                        calls.append(("run", sid, selected, model)) or {"status": "done", "finished": []})
    monkeypatch.setattr(sw, "finish", lambda conn, sid, status: calls.append(("finish", sid, status)))
    monkeypatch.setattr(cli.board, "write", lambda conn, repo: tmp_path / "b.html")
    return calls


def test_sweep_top_creates_plans_and_runs(faked):
    assert cli.main(["--repo", "o/r", "--sweep", "--top", "3"]) == 0
    assert faked == [("collect", "o/r"), ("create", 3, None), ("plan", 3, None), ("run", 9, [5, 3], "sonnet")]


def test_every_sweep_refreshes_issues_and_prs_first(faked):
    cli.main(["--repo", "o/r", "--sweep", "--top", "1"])
    assert faked[0] == ("collect", "o/r")


def test_sweep_named_issues(faked):
    cli.main(["--repo", "o/r", "--sweep", "--issues", "424,431", "--model", "opus"])
    assert ("plan", None, [424, 431]) in faked
    assert faked[-1] == ("run", 9, [5, 3], "opus")


def test_sweep_without_top_or_issues_defaults_to_ten(faked):
    cli.main(["--repo", "o/r", "--sweep"])
    assert ("plan", 10, None) in faked


def test_given_sweep_id_skips_create(faked):
    cli.main(["--repo", "o/r", "--sweep", "--sweep-id", "42", "--top", "2"])
    assert [c[0] for c in faked] == ["collect", "plan", "run"]
    assert faked[-1][1] == 42


def test_a_crashed_sweep_is_marked_error_and_exits_1(faked, monkeypatch):
    def boom(conn, repo, sid, selected, model):
        raise RuntimeError("db went away")
    monkeypatch.setattr(sw, "run", boom)
    assert cli.main(["--repo", "o/r", "--sweep", "--top", "2"]) == 1
    assert faked[-1] == ("finish", 9, "error")


@pytest.mark.parametrize("argv", [
    ["--repo", "o/r", "--issue", "5", "--top", "3"],
    ["--repo", "o/r", "--list", "--issues", "1,2"],
    ["--repo", "o/r", "--sweep", "--top", "0"],
    ["--repo", "o/r", "--sweep", "--top", "101"],
    ["--repo", "o/r", "--sweep", "--issues", "a,b"],
])
def test_bad_sweep_arguments_exit_with_a_usage_error(faked, argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2


# --- review round 1: the crash handler must work even when the connection or collect is what failed -------

def test_a_crash_rolls_back_before_marking_error(faked, monkeypatch):
    def boom(conn, repo, sid, selected, model):
        raise RuntimeError("current transaction is aborted")
    monkeypatch.setattr(sw, "run", boom)
    assert cli.main(["--repo", "o/r", "--sweep", "--top", "2"]) == 1
    assert faked[-2:] == [("rollback",), ("finish", 9, "error")]


def test_a_gh_failure_while_collecting_marks_the_given_sweep_error(faked, monkeypatch):
    def gh_fails(repo):
        raise SystemExit("gh issue list -R o/r failed: HTTP 502")  # collect.gh exits on failure
    monkeypatch.setattr(cli, "collect", gh_fails)
    assert cli.main(["--repo", "o/r", "--sweep", "--sweep-id", "42", "--top", "2"]) == 1
    assert faked[-1] == ("finish", 42, "error")


def test_the_crash_message_survives_a_failing_finish(faked, monkeypatch, capsys):
    def boom(conn, repo, sid, selected, model):
        raise RuntimeError("db went away")

    def finish_fails(conn, sid, status):
        raise RuntimeError("connection is closed")
    monkeypatch.setattr(sw, "run", boom)
    monkeypatch.setattr(sw, "finish", finish_fails)
    assert cli.main(["--repo", "o/r", "--sweep", "--top", "2"]) == 1
    assert "db went away" in capsys.readouterr().err
