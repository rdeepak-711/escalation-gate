"""MCP server tests: four tools that start, watch, read and stop a sweep. Postgres and the subprocess are faked."""
import asyncio
import json
import sys

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

import gate.mcp_server as ms


class FakeConn:
    def close(self):
        pass


class FakePopen:
    calls = []

    def __init__(self, cmd, **kwargs):
        FakePopen.calls.append((cmd, kwargs))
        self.pid = 999


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = {"latest": None, "alive": True, "created": [], "pids": [], "errors": [], "stops": [], "rows": []}
    FakePopen.calls = []
    monkeypatch.setattr(ms, "connect", lambda: FakeConn())
    monkeypatch.setattr(ms, "SITE_DIR", tmp_path / "site")
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(ms, "latest_sweep", lambda conn, repo: state["latest"])
    monkeypatch.setattr(ms, "pid_alive", lambda pid: state["alive"])
    monkeypatch.setattr(ms, "create_sweep",
                        lambda conn, repo, top, issues, model: state["created"].append((repo, top, issues, model)) or 42)
    monkeypatch.setattr(ms, "set_pid", lambda conn, sid, pid, log: state["pids"].append((sid, pid, str(log))))
    monkeypatch.setattr(ms, "mark_error", lambda conn, sid: state["errors"].append(sid))
    monkeypatch.setattr(ms, "request_stop", lambda conn, sid: state["stops"].append(sid))
    monkeypatch.setattr(ms, "board_rows", lambda conn, repo: state["rows"])
    monkeypatch.setattr(ms.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ms, "preflight", lambda: None)  # gh/claude checks have their own tests
    return state, tmp_path


def sweep(status="running", pid=111, planned=(5, 3, 8), finished=(), current=3):
    return {"id": 7, "status": status, "pid": pid, "planned": list(planned), "current": current,
            "finished": [{"issue": n, "outcome": o} for n, o in finished], "log": "/x.log"}


def row(n, status="verified", result="merge #9", best=None, inv=1):
    return {"number": n, "title": f"issue {n}", "kind": "bug", "status": status, "inv": inv, "result": result,
            "url": f"https://github.com/o/r/issues/{n}", "best": best}


# --- validation ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("repo", ["", "nope", "o/r/x", "../etc/passwd", "o/r;rm -rf", "o r/x",
                                  "../etc", "o/..", "o/.", "./r", "o.x/r", "o_x/r"])
def test_bad_repo_is_refused_by_every_tool(env, repo):
    for tool in (ms.start_sweep, ms.sweep_status, ms.get_board, ms.stop_sweep):
        with pytest.raises(ToolError):
            tool(repo)


@pytest.mark.parametrize("repo", ["HackerRank-Engineering/hiring-agent", "o/.github", "o/r.js", "o-1/my_repo"])
def test_real_repo_names_are_accepted(env, repo):
    assert ms.sweep_status(repo) == {"status": "none"}


@pytest.mark.parametrize("kwargs", [{"top": 0}, {"top": 101}, {"issues": list(range(51))}, {"model": "gpt-4"}])
def test_bad_sweep_arguments_are_refused(env, kwargs):
    with pytest.raises(ToolError):
        ms.start_sweep("o/r", **kwargs)
    assert FakePopen.calls == []


# --- start_sweep --------------------------------------------------------------------------------------------

def test_start_spawns_the_sweep_in_the_background_and_returns_at_once(env):
    state, tmp = env
    out = ms.start_sweep("o/r")
    cmd, kwargs = FakePopen.calls[0]
    assert cmd == [sys.executable, "-m", "gate.cli", "--repo", "o/r", "--sweep", "--sweep-id", "42",
                   "--top", "10", "--model", "sonnet"]
    assert kwargs["start_new_session"] is True
    assert state["created"] == [("o/r", 10, None, "sonnet")]
    assert state["pids"][0][:2] == (42, 999)
    assert state["pids"][0][2].endswith("sweep-42.log")
    assert out["sweep_id"] == 42 and out["status"] == "running"
    assert out["board"] == str(tmp / "site" / "r" / "index.html")


def test_start_with_named_issues_passes_them_instead_of_top(env):
    ms.start_sweep("o/r", issues=[424, 431], model="opus")
    cmd, _ = FakePopen.calls[0]
    assert "--issues" in cmd and cmd[cmd.index("--issues") + 1] == "424,431"
    assert "--top" not in cmd
    assert cmd[-2:] == ["--model", "opus"]


def test_start_refuses_while_a_live_sweep_is_running(env):
    state, _ = env
    state["latest"] = sweep("running")
    with pytest.raises(ToolError, match="already running"):
        ms.start_sweep("o/r")
    assert FakePopen.calls == []


def test_start_clears_a_dead_sweep_and_goes_ahead(env):
    state, _ = env
    state["latest"] = sweep("running")
    state["alive"] = False
    ms.start_sweep("o/r")
    assert state["errors"] == [7]
    assert len(FakePopen.calls) == 1


def test_a_finished_sweep_does_not_block_a_new_one(env):
    state, _ = env
    state["latest"] = sweep("done")
    ms.start_sweep("o/r")
    assert len(FakePopen.calls) == 1 and state["errors"] == []


# --- sweep_status -------------------------------------------------------------------------------------------

def test_status_when_never_swept(env):
    assert ms.sweep_status("o/r") == {"status": "none"}


def test_status_reports_progress_with_each_finished_result(env):
    state, tmp = env
    state["latest"] = sweep(finished=[(5, "verified")], current=3)
    state["rows"] = [row(5, result="merge #9"), row(3, status=None, result="", inv=None)]
    out = ms.sweep_status("o/r")
    assert out["sweep_id"] == 7 and out["status"] == "running"
    assert out["planned"] == [5, 3, 8] and out["total"] == 3 and out["done"] == 1
    assert out["current"] == 3
    assert out["finished"] == [{"issue": 5, "outcome": "verified", "result": "merge #9"}]
    assert out["board"] == str(tmp / "site" / "r" / "index.html")


# --- get_board ----------------------------------------------------------------------------------------------

def test_board_lists_investigated_issues_with_best_pr_and_page(env):
    state, tmp = env
    best = {"pr": 9, "url": "https://github.com/o/r/pull/9", "verdict": "fixes", "why": "w",
            "confidence": {"level": "high", "checks": [], "text": "high: cause lines checked"}}
    state["rows"] = [row(5, best=best), row(3, status=None, result="", inv=None)]
    out = ms.get_board("o/r")
    assert out["board"] == str(tmp / "site" / "r" / "index.html")
    assert out["counts"]["open"] == 2 and out["counts"]["done"] == 1
    assert [r["number"] for r in out["rows"]] == [5]
    r = out["rows"][0]
    assert r["url"] == "https://github.com/o/r/issues/5" and r["best"]["pr"] == 9
    assert r["page"] == str(tmp / "site" / "r" / "issues" / "5.html")


# --- stop_sweep ---------------------------------------------------------------------------------------------

def test_stop_asks_a_running_sweep_to_stop(env):
    state, _ = env
    state["latest"] = sweep("running")
    assert ms.stop_sweep("o/r") == {"sweep_id": 7, "status": "stopping"}
    assert state["stops"] == [7]


def test_stop_with_nothing_running(env):
    state, _ = env
    state["latest"] = sweep("done")
    assert ms.stop_sweep("o/r") == {"status": "none"}
    assert state["stops"] == []


# --- end to end over the MCP protocol (in-process) ---------------------------------------------------------

def test_client_round_trip(env):
    state, _ = env
    state["rows"] = [row(5)]

    async def go():
        async with Client(ms.server) as client:
            names = sorted(t.name for t in (await client.list_tools()).tools)
            result = await client.call_tool("get_board", {"repo": "o/r"})
            bad = await client.call_tool("get_board", {"repo": "nope"})
            return names, result, bad

    names, result, bad = asyncio.run(go())
    assert names == ["get_board", "start_sweep", "stop_sweep", "sweep_status"]
    assert not result.is_error
    payload = result.structured_content or json.loads(result.content[0].text)
    assert payload["rows"][0]["number"] == 5
    assert bad.is_error


def test_instructions_tell_the_agent_to_name_the_other_fixes_and_what_checked_means():
    text = ms.INSTRUCTIONS
    assert "others" in text and "duplicate" in text
    assert "fix lines checked" in text and "not that they fix" in text
