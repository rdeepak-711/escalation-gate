"""End to end over stdio: start `python -m gate.mcp_server` as its own process, the way Claude Code runs it, and talk
to it with the MCP client against a real throwaway database. No sweep is started (that would call gh and claude)."""
import asyncio
import json
import os
import sys

from mcp import Client, StdioServerParameters

import gate.db as gdb
from conftest import add_inv, add_issue, add_pr
from gate.investigate import ROOT


def payload(result):
    return result.structured_content or json.loads(result.content[0].text)


def test_the_server_answers_over_stdio(db):
    add_issue(db, 2)
    add_issue(db, 1)
    add_pr(db, 10, body="fixes #2")
    inv = add_inv(db, 2, "verified", report={"cause": "c", "repro": [], "evidence": [], "confidence": "high"})
    db.execute("INSERT INTO pr_verdicts (investigation_id, pr, reasons, verdict, why, merge) "
               "VALUES (%s, 10, '{}', 'fixes', 'clamps', 'clean')", (inv,))
    db.commit()
    params = StdioServerParameters(command=sys.executable, args=["-m", "gate.mcp_server"], cwd=str(ROOT),
                                   env={**os.environ, "DATABASE_URL": gdb.DATABASE_URL})

    async def go():
        async with Client(params) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            status = await client.call_tool("sweep_status", {"repo": "o/r"})
            board = await client.call_tool("get_board", {"repo": "o/r"})
            bad_model = await client.call_tool("start_sweep", {"repo": "o/r", "model": "gpt-4"})
            bad_repo = await client.call_tool("stop_sweep", {"repo": "../etc"})
            return tools, status, board, bad_model, bad_repo

    tools, status, board, bad_model, bad_repo = asyncio.run(asyncio.wait_for(go(), 60))
    assert sorted(tools) == ["get_board", "start_sweep", "stop_sweep", "sweep_status"]
    assert set(tools["start_sweep"].input_schema["properties"]) == {"repo", "top", "issues", "model", "max_usd"}
    assert payload(status) == {"status": "none"}
    rows = payload(board)["rows"]
    assert [r["number"] for r in rows] == [2] and rows[0]["best"]["pr"] == 10
    assert bad_model.is_error and bad_repo.is_error
