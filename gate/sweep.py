"""Pure issue ranking and selection helpers for repo sweeps."""
import re
import json
import os

from gate import board, candidates, effects, investigate, packet
from gate.board import kind


def mentions(text: str | None, number: int) -> bool:
    if not text:
        return False
    n = re.escape(str(number))
    return bool(re.search(rf"(?i)(?:#{n}(?!\d)|/issues/{n}(?!\d))", text))


def rank_issues(issues: list[dict], prs: list[dict]) -> list[dict]:
    ranked = []
    for issue in issues:
        number = issue["number"]
        linked = sorted(pr["number"] for pr in prs
                        if mentions(pr.get("title"), number) or mentions(pr.get("body"), number))
        title = issue["title"]
        ranked.append({"number": number, "title": title,
                       "kind": kind(title, issue.get("labels", [])),
                       "linked_prs": linked,
                       "engagement": issue.get("thumbs_up", 0) + issue.get("comments", 0)})
    return sorted(ranked, key=lambda row: (row["kind"] == "feature", -len(row["linked_prs"]),
                                           -row["engagement"], row["number"]))


def select(ranked: list[dict], done: set[int], top: int | None = None,
           issues: list[int] | None = None) -> list[int]:
    available = {row["number"]: row for row in ranked}
    if issues is not None:
        seen = set()
        selected = []
        for number in issues:
            if number not in seen and number not in done and number in available:
                selected.append(number)
                seen.add(number)
        return selected
    top = 10 if top is None else top
    selected = []
    for row in ranked:
        if len(selected) >= top:
            break
        if row["kind"] != "feature" and row["number"] not in done:
            selected.append(row["number"])
    return selected


def process_issue(conn, repo: str, issue: int, model: str) -> dict:
    inv_id, outcome = investigate.run(conn, repo, issue, None, model)
    status = outcome["status"]
    if status in ("verified", "needs_human"):
        candidates.run(conn, inv_id, model)
        effects.run(conn, inv_id, model)
        packet.write(conn, inv_id)
    cost = conn.execute(
        "SELECT coalesce((SELECT sum(c) FROM unnest(costs_usd) c), 0) + judge_cost_usd + effects_cost_usd "
        "FROM investigations WHERE id = %s", (inv_id,)).fetchone()[0]
    return {"status": status, "cost": cost, "error": None}


def create(conn, repo: str, top: int | None, issues: list[int] | None, model: str, max_usd: float | None) -> int:
    sweep_id = conn.execute(
        "INSERT INTO sweeps (repo, status, pid, top, issues, model, max_usd) "
        "VALUES (%s, 'running', %s, %s, %s, %s, %s) RETURNING id",
        (repo, os.getpid(), top, issues, model, max_usd),
    ).fetchone()[0]
    conn.commit()
    return sweep_id


def set_planned(conn, sweep_id: int, planned: list[int]) -> None:
    conn.execute("UPDATE sweeps SET planned = %s WHERE id = %s", (planned, sweep_id))
    conn.commit()


def set_current(conn, sweep_id: int, issue: int | None) -> None:
    conn.execute("UPDATE sweeps SET current = %s WHERE id = %s", (issue, sweep_id))
    conn.commit()


def record(conn, sweep_id: int, issue: int, outcome: dict) -> None:
    item = {"issue": issue, "outcome": outcome["status"], "cost": outcome["cost"], "error": outcome["error"]}
    conn.execute("UPDATE sweeps SET finished = finished || %s::jsonb, cost_usd = cost_usd + %s WHERE id = %s",
                 (json.dumps([item]), outcome["cost"], sweep_id))
    conn.commit()


def spent(conn, sweep_id: int) -> float:
    return conn.execute("SELECT cost_usd FROM sweeps WHERE id = %s", (sweep_id,)).fetchone()[0]


def stop_requested(conn, sweep_id: int) -> bool:
    row = conn.execute("SELECT status FROM sweeps WHERE id = %s", (sweep_id,)).fetchone()
    return bool(row and row[0] == "stopping")


def finish(conn, sweep_id: int, status: str, stop_reason: str | None = None) -> None:
    conn.execute("UPDATE sweeps SET status = %s, stop_reason = %s, finished_at = now(), current = NULL WHERE id = %s",
                 (status, stop_reason, sweep_id))
    conn.commit()


def error_text(exc: BaseException) -> str:
    name = type(exc).__name__
    if type(exc).__module__ != "builtins":
        name = f"{type(exc).__module__}.{name}"
    first_line = str(exc).splitlines()[0] if str(exc) else ""
    return f"{name}: {first_line}"[:200]


def run(conn, repo: str, sweep_id: int, selected: list[int], model: str, max_usd: float | None = None) -> dict:
    set_planned(conn, sweep_id, selected)
    finished = []
    for issue in selected:
        if stop_requested(conn, sweep_id):
            finish(conn, sweep_id, "stopped", "requested")
            return {"status": "stopped", "stop_reason": "requested", "finished": finished}
        if max_usd is not None and spent(conn, sweep_id) >= max_usd:
            finish(conn, sweep_id, "stopped", "cost cap")
            return {"status": "stopped", "stop_reason": "cost cap", "finished": finished}
        set_current(conn, sweep_id, issue)
        try:
            outcome = process_issue(conn, repo, issue, model)
        except (Exception, SystemExit) as exc:
            conn.rollback()
            outcome = {"status": "error", "cost": 0, "error": error_text(exc)}
        record(conn, sweep_id, issue, outcome)
        finished.append({"issue": issue, "outcome": outcome["status"], "cost": outcome["cost"],
                         "error": outcome["error"]})
        board.write(conn, repo)
    finish(conn, sweep_id, "done")
    return {"status": "done", "stop_reason": None, "finished": finished}


def plan(conn, repo: str, top: int | None, issues: list[int] | None) -> list[int]:
    issue_rows = conn.execute(
        "SELECT number, title, labels, thumbs_up, comments FROM issues WHERE repo = %s", (repo,)
    ).fetchall()
    pr_rows = conn.execute("SELECT number, title, body FROM prs WHERE repo = %s", (repo,)).fetchall()
    ranked = rank_issues(
        [{"number": n, "title": title, "labels": labels, "thumbs_up": thumbs, "comments": comments}
         for n, title, labels, thumbs, comments in issue_rows],
        [{"number": n, "title": title, "body": body} for n, title, body in pr_rows],
    )
    done = {r[0] for r in conn.execute(
        "SELECT DISTINCT issue FROM investigations WHERE repo = %s AND issue IS NOT NULL AND NOT hidden "
        "AND status IN ('verified', 'needs_human')", (repo,))}
    return select(ranked, done, top, issues)
