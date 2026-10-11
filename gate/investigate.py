"""Step 04: investigate one issue or symptom with Claude Code, check every citation, retry once."""
import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

from psycopg.types.json import Jsonb

from gate.check import check
from gate.db import connect

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "out" / "runs"
TIMEOUT_S = 360
DISALLOWED = "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Task"

SCHEMA = {
    "type": "object",
    "required": ["cause", "repro", "evidence", "confidence", "chain"],
    "properties": {
        "cause": {"type": "string"},
        "repro": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file", "line", "quote", "why"],
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "quote": {"type": "string", "maxLength": 200},
                    "why": {"type": "string"},
                },
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "chain": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["step", "evidence"],
                "properties": {
                    "step": {"type": "string", "maxLength": 60},
                    "evidence": {"type": "integer"},
                },
            },
        },
    },
}

PROMPT_TEMPLATE = """Investigate this problem report in the repository in the current directory.

{report}

Rules:
1. Find the root cause by reading the code. Do not trust the report's own claims about the code: confirm each one in the files.
2. Every evidence item must give the file path relative to the repository root, the line number, and a quote copied exactly from that line (at most 200 characters; if the line is longer, quote only the relevant part).
3. Give reproduction steps a developer can follow.
4. If you cannot find evidence, say so and set confidence to "low".
5. Give the chain: the cause as ordered steps, from where the problem starts to where it shows. Each step is a short label (at most 8 words) plus the number of the evidence item that proves it (1 = the first evidence item). Use only the steps your evidence supports.
"""

RETRY_TEMPLATE = """Some of your evidence did not match the code:

{failures}

Open each cited file again, fix or remove those evidence items, and answer again in the same format."""

def run_claude(prompt: str, repo_dir: Path, session_id: str, resume: bool, log: Path, model: str | None = None,
               schema: dict = SCHEMA, tools: str = "Read,Grep,Glob") -> dict | None:
    """Run one Claude Code turn, streaming every event into `log`.
    Returns the final result event, or None if it timed out (the log keeps everything up to the kill).
    """
    cmd = ["claude", "-p", "--safe-mode", "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(schema), "--tools", tools, "--disallowedTools", DISALLOWED]
    cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    if model:
        cmd += ["--model", model]

    with log.open("a") as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.PIPE,text=True,cwd=repo_dir)
        try:
            _, err = proc.communicate(prompt, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return None

    if proc.returncode!=0:
        return RuntimeError(f"claude exited with {proc.returncode}: {err}")
    return json.loads(log.read_text().splitlines()[-1])

def investigate(report_text: str, repo_dir: Path, model: str | None = None) -> dict:
    session_id=str(uuid.uuid4())
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log = RUNS_DIR / f"{session_id}.jsonl"

    prompt = PROMPT_TEMPLATE.format(report=report_text)
    report, failures, costs = None, [], []
    status = "error"
    for attempt in (1, 2):
        result = run_claude(prompt, repo_dir, session_id, resume=attempt > 1, log=log, model=model)
        if result is None:
            status, failures = "timeout", [f"timed out after {TIMEOUT_S} s"]
            break
        costs.append(result.get("total_cost_usd", 0.0))
        report = result.get("structured_output")
        if not report:
            status, failures = "error", [f"no structured output ({result.get('subtype')})"]
            break
        failures = check(report, repo_dir)
        if not failures:
            status = "verified"
            break
        status = "needs_human"
        prompt = RETRY_TEMPLATE.format(failures="\n".join(failures))

    return {"session_id": session_id, "status": status, "attempts": attempt,
            "report": report, "failures": failures, "costs_usd": costs, "log": str(log)}


def save(conn, repo: str, issue: int | None, symptom: str | None, outcome: dict) -> int:
    """Store one investigation; return its id."""
    row = conn.execute(
        """INSERT INTO investigations
               (repo, issue, symptom, session_id, status, attempts, report, failures, costs_usd)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (repo, issue, symptom, outcome["session_id"], outcome["status"], outcome["attempts"],
         Jsonb(outcome["report"]) if outcome["report"] else None,
         outcome["failures"], outcome["costs_usd"]),
    ).fetchone()
    conn.commit()
    return row[0]


def run(conn, repo: str, issue: int | None, symptom: str | None, model: str | None = None) -> tuple[int, dict]:
    """Investigate one issue (read from Postgres) or symptom, save it, print the outcome; return (id, outcome)."""
    repo_dir = ROOT / "vendor" / repo.split("/")[1]
    if issue:
        row = conn.execute("SELECT title, body FROM issues WHERE repo = %s AND number = %s",
                           (repo, issue)).fetchone()
        if row is None:
            sys.exit(f"issue #{issue} is not in the database: run gate.collect first, or it is closed")
        report_text = f"Issue #{issue}: {row[0]}\n\n{row[1]}"
    else:
        report_text = f"Reported symptom: {symptom}"

    outcome = investigate(report_text, repo_dir, model=model)
    inv_id = save(conn, repo, issue, symptom, outcome)

    print(f"investigation {inv_id}: {outcome['status']} after {outcome['attempts']} attempt(s), "
          f"cost ${sum(outcome['costs_usd']):.2f}, log {outcome['log']}")
    if outcome["report"]:
        print(json.dumps(outcome["report"], indent=2))
    for failure in outcome["failures"]:
        print("FAILED:", failure)
    return inv_id, outcome


def main() -> int:
    parser = argparse.ArgumentParser(description="Investigate one issue or symptom in a collected repo.")
    parser.add_argument("--repo", required=True, help="owner/name, already collected")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--issue", type=int)
    target.add_argument("--symptom")
    parser.add_argument("--model", help="e.g. sonnet or opus; default is Claude Code's own")
    args = parser.parse_args()

    conn = connect()
    _, outcome = run(conn, args.repo, args.issue, args.symptom, args.model)
    conn.close()
    return 0 if outcome["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())