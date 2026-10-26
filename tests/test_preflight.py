"""Tier 1: when Postgres, gh or claude is missing, every tool answers with one plain sentence, never a traceback."""
import asyncio
import subprocess

import psycopg
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

import gate.mcp_server as ms


class Done:
    def __init__(self, code):
        self.returncode = code


@pytest.fixture
def ready(monkeypatch):
    """Everything installed; tests break one thing at a time."""
    state = {"which": {"gh": "/usr/bin/gh", "claude": "/usr/bin/claude"}, "auth": 0}
    monkeypatch.setattr(ms.shutil, "which", lambda name: state["which"].get(name))
    monkeypatch.setattr(ms.subprocess, "run", lambda cmd, **kw: Done(state["auth"]))
    monkeypatch.setattr(ms, "connect", lambda: (_ for _ in ()).throw(AssertionError("connect must not be called")))
    return state


def test_preflight_passes_when_everything_is_there(ready):
    ms.preflight()


def test_missing_gh_names_the_github_cli(ready):
    ready["which"]["gh"] = None
    with pytest.raises(ToolError, match="GitHub CLI"):
        ms.preflight()


def test_gh_not_logged_in_says_how(ready):
    ready["auth"] = 1
    with pytest.raises(ToolError, match="gh auth login"):
        ms.preflight()


def test_missing_claude_names_claude_code(ready):
    ready["which"]["claude"] = None
    with pytest.raises(ToolError, match="Claude Code"):
        ms.preflight()


def test_start_sweep_runs_preflight_before_touching_the_database(ready, monkeypatch):
    ready["which"]["gh"] = None
    with pytest.raises(ToolError, match="GitHub CLI"):
        ms.start_sweep("o/r")


def down():
    raise psycopg.OperationalError('connection failed: could not connect to server: Connection refused')


@pytest.mark.parametrize("tool", ["start_sweep", "sweep_status", "get_board", "stop_sweep"])
def test_postgres_down_is_one_sentence_from_every_tool(ready, monkeypatch, tool):
    monkeypatch.setattr(ms, "connect", down)
    with pytest.raises(ToolError, match="Postgres is not reachable at postgresql://") as e:
        getattr(ms, tool)("o/r")
    assert "createdb escalation_gate" in str(e.value)
    assert "Traceback" not in str(e.value)


def test_over_the_protocol_the_sentence_reaches_the_agent(ready, monkeypatch):
    monkeypatch.setattr(ms, "connect", down)

    async def go():
        async with Client(ms.server) as client:
            return await client.call_tool("get_board", {"repo": "o/r"})

    result = asyncio.run(go())
    assert result.is_error
    text = result.content[0].text
    assert "Postgres is not reachable" in text and "createdb escalation_gate" in text
    assert "OperationalError" not in text
