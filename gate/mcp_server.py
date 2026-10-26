"""stdio MCP tools for starting and inspecting escalation-gate repo sweeps."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from gate import board
from gate.db import DATABASE_URL, connect
from gate.investigate import ROOT

SITE_DIR = ROOT / "site"
OUT_DIR = ROOT / "out"
INSTRUCTIONS = """Start with start_sweep, which returns immediately while the sweep runs in the background; """
INSTRUCTIONS += """sweeps take minutes per issue. Check progress with sweep_status. When it is done, read get_board """
INSTRUCTIONS += """and report per issue which PR to merge with its confidence text, what is only partly fixed, """
INSTRUCTIONS += """and what has no fix. Give the user the board path to open. Never present a model judgement as checked."""
INSTRUCTIONS += """ For each issue, also name the others PRs that fix or partly fix it, and say that once one is merged """
INSTRUCTIONS += """the rest likely duplicate it and can be closed. "fix lines checked" means the quoted lines are really in """
INSTRUCTIONS += """the diff, not that they fix the bug; whether they fix it is the model's judgement from reading the diff."""

server = MCPServer("escalation-gate", instructions=INSTRUCTIONS)
REPO = re.compile(r"^[A-Za-z0-9-]+/(?!\.\.?$)[A-Za-z0-9_.-]+$")


def validate_repo(repo: str) -> None:
    if not isinstance(repo, str) or not REPO.fullmatch(repo):
        raise ToolError("repo must be an owner/name GitHub repository")


def preflight() -> None:
    """Checks the tools a sweep needs; each failure is one sentence the agent can act on."""
    if shutil.which("gh") is None:
        raise ToolError("the GitHub CLI (gh) is not installed: install it from https://cli.github.com/")
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        raise ToolError("the GitHub CLI is not logged in: run gh auth login")
    if shutil.which("claude") is None:
        raise ToolError("Claude Code (claude) is not on PATH: install it and log in before sweeping")


def open_db():
    """connect(), with an unreachable Postgres reported as one sentence."""
    try:
        return connect()
    except psycopg.OperationalError:
        raise ToolError(f"Postgres is not reachable at {DATABASE_URL}: start it and run createdb escalation_gate")


def latest_sweep(conn, repo: str):
    row = conn.execute(
        "SELECT id, status, pid, planned, current, finished, log FROM sweeps WHERE repo = %s ORDER BY id DESC LIMIT 1",
        (repo,),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("id", "status", "pid", "planned", "current", "finished", "log"), row))


def create_sweep(conn, repo: str, top: int | None, issues: list[int] | None, model: str) -> int:
    sweep_id = conn.execute(
        "INSERT INTO sweeps (repo, status, top, issues, model) VALUES (%s, 'running', %s, %s, %s) RETURNING id",
        (repo, top, issues, model),
    ).fetchone()[0]
    conn.commit()
    return sweep_id


def set_pid(conn, sweep_id: int, pid: int, log: Path) -> None:
    conn.execute("UPDATE sweeps SET pid = %s, log = %s WHERE id = %s", (pid, str(log), sweep_id))
    conn.commit()


def mark_error(conn, sweep_id: int) -> None:
    conn.execute("UPDATE sweeps SET status = 'error', finished_at = now(), current = NULL WHERE id = %s",
                 (sweep_id,))
    conn.commit()


def request_stop(conn, sweep_id: int) -> None:
    conn.execute("UPDATE sweeps SET status = 'stopping' WHERE id = %s AND status = 'running'", (sweep_id,))
    conn.commit()


def pid_alive(pid: int | None) -> bool:
    """True while the process runs; an exited child of ours is reaped here and reads as dead."""
    if not pid:
        return False
    try:
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def board_rows(conn, repo: str) -> list[dict]:
    return board.rows(conn, repo)


def board_path(repo: str) -> str:
    return str((SITE_DIR / repo.split("/")[1] / "index.html").resolve())


@server.tool()
def start_sweep(repo: str, top: int = 10, issues: list[int] | None = None, model: str = "sonnet") -> dict:
    validate_repo(repo)
    if not 1 <= top <= 100:
        raise ToolError("top must be between 1 and 100")
    if issues is not None and (len(issues) > 50 or any(not isinstance(n, int) or n < 1 for n in issues)):
        raise ToolError("issues must contain at most 50 positive issue numbers")
    if model not in ("sonnet", "opus", "haiku"):
        raise ToolError("model must be sonnet, opus, or haiku")
    preflight()
    conn = open_db()
    try:
        latest = latest_sweep(conn, repo)
        if latest and latest["status"] in ("running", "stopping"):
            if pid_alive(latest.get("pid")):
                raise ToolError("a sweep is already running for this repository")
            mark_error(conn, latest["id"])
        sweep_id = create_sweep(conn, repo, None if issues is not None else top, issues, model)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        log = OUT_DIR / f"sweep-{sweep_id}.log"
        command = [sys.executable, "-m", "gate.cli", "--repo", repo, "--sweep", "--sweep-id", str(sweep_id)]
        command += ["--issues", ",".join(map(str, issues))] if issues is not None else ["--top", str(top)]
        command += ["--model", model]
        with log.open("w") as stream:
            proc = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        set_pid(conn, sweep_id, proc.pid, log)
        return {"sweep_id": sweep_id, "status": "running", "board": board_path(repo), "log": str(log.resolve())}
    finally:
        conn.close()


@server.tool()
def sweep_status(repo: str) -> dict:
    validate_repo(repo)
    conn = open_db()
    try:
        latest = latest_sweep(conn, repo)
        if not latest:
            return {"status": "none"}
        if latest["status"] in ("running", "stopping") and not pid_alive(latest.get("pid")):
            mark_error(conn, latest["id"])
            latest["status"] = "error"
        rows = {row["number"]: row for row in board_rows(conn, repo)}
        finished = [{**item, "result": rows.get(item["issue"], {}).get("result", "")}
                    for item in latest.get("finished", [])]
        planned = latest.get("planned") or []
        return {"sweep_id": latest["id"], "status": latest["status"], "planned": planned,
                "done": len(finished), "total": len(planned), "current": latest.get("current"),
                "finished": finished, "board": board_path(repo)}
    finally:
        conn.close()


@server.tool()
def get_board(repo: str) -> dict:
    validate_repo(repo)
    conn = open_db()
    try:
        rows = board_rows(conn, repo)
        base = SITE_DIR / repo.split("/")[1]
        out_rows = [{"number": row["number"], "title": row["title"], "url": row["url"],
                     "status": row["status"], "result": row["result"], "best": row["best"],
                     "others": row.get("others", []),
                     "page": str((base / "issues" / f"{row['number']}.html").resolve())}
                    for row in rows if row.get("inv")]
        return {"board": board_path(repo), "counts": board.counts(rows), "rows": out_rows}
    finally:
        conn.close()


@server.tool()
def stop_sweep(repo: str) -> dict:
    validate_repo(repo)
    conn = open_db()
    try:
        latest = latest_sweep(conn, repo)
        if not latest or latest["status"] not in ("running", "stopping"):
            return {"status": "none"}
        request_stop(conn, latest["id"])
        return {"sweep_id": latest["id"], "status": "stopping"}
    finally:
        conn.close()


if __name__ == "__main__":
    server.run()
