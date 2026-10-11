"""Step 07 tests: the one command runs the stages in order and skips what it should, with every stage faked."""
import pytest

import gate.cli as cli


class FakeConn:
    def __init__(self, collected):
        self.collected = collected

    def execute(self, sql, params):
        return self

    def fetchone(self):
        return (self.collected,)

    def close(self):
        pass


@pytest.fixture
def stages(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "VENDOR", tmp_path)
    monkeypatch.setattr(cli, "clone", lambda repo, path: calls.append(("clone", repo)))
    monkeypatch.setattr(cli, "collect", lambda repo: calls.append(("collect", repo)))
    monkeypatch.setattr(cli.investigate, "run",
                        lambda conn, repo, issue, symptom, model: (calls.append(("investigate", issue, symptom))
                                                                   or (7, {"status": calls_status[0]})))
    monkeypatch.setattr(cli.candidates, "run", lambda conn, inv_id, model: calls.append(("candidates", inv_id)) or [])
    monkeypatch.setattr(cli.packet, "write", lambda conn, inv_id: calls.append(("packet", inv_id)) or tmp_path / "p.html")
    calls_status = ["verified"]
    return calls, calls_status


def run(monkeypatch, argv, collected=0):
    monkeypatch.setattr(cli, "connect", lambda: FakeConn(collected))
    return cli.main(argv)


def test_new_repo_is_cloned_collected_and_run_end_to_end(stages, monkeypatch):
    calls, _ = stages
    assert run(monkeypatch, ["--repo", "o/r", "--issue", "5"]) == 0
    assert [c[0] for c in calls] == ["clone", "collect", "investigate", "candidates", "packet"]


def test_collected_repo_skips_clone_and_collect(stages, monkeypatch, tmp_path):
    calls, _ = stages
    (tmp_path / "r").mkdir()
    run(monkeypatch, ["--repo", "o/r", "--symptom", "boom"], collected=12)
    assert [c[0] for c in calls] == ["investigate", "candidates", "packet"]
    assert calls[0] == ("investigate", None, "boom")


def test_refresh_collects_again(stages, monkeypatch, tmp_path):
    calls, _ = stages
    (tmp_path / "r").mkdir()
    run(monkeypatch, ["--repo", "o/r", "--issue", "5", "--refresh"], collected=12)
    assert [c[0] for c in calls][0] == "collect"


def test_a_failed_investigation_stops_before_the_prs(stages, monkeypatch, tmp_path):
    calls, status = stages
    status[0] = "timeout"
    (tmp_path / "r").mkdir()
    assert run(monkeypatch, ["--repo", "o/r", "--issue", "5"], collected=12) == 1
    assert [c[0] for c in calls] == ["investigate"]


def test_needs_human_still_gets_a_packet_but_exits_1(stages, monkeypatch, tmp_path):
    calls, status = stages
    status[0] = "needs_human"
    (tmp_path / "r").mkdir()
    assert run(monkeypatch, ["--repo", "o/r", "--issue", "5"], collected=12) == 1
    assert [c[0] for c in calls] == ["investigate", "candidates", "packet"]
