"""The repo board: every open issue with its status (⬜ not investigated, ✅ verified, ⚠️ needs a human, ✗ failed),
as a terminal table and as a web page that links each done issue to its packet."""
import re
import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from gate import packet
from gate.investigate import ROOT

SITE_DIR = ROOT / "site"
SYMBOL = {None: "⬜", "verified": "✅", "needs_human": "⚠️", "timeout": "✗", "error": "✗"}
BUG = re.compile(r"^\W*(bug|fix|issue)\b|error|exception|crash|fails?\b|broken|incorrect|wrong|never|not (applied|working)",
                 re.I)
FEATURE = re.compile(r"^\W*(feat|feature|add|enhancement|proposal|improvement|support|request|idea|implement)\b", re.I)


def kind(title: str, labels: list[str]) -> str:
    """bug / feature / other, from labels first, then the title."""
    lowered = [label.lower() for label in labels]
    if "bug" in lowered:
        return "bug"
    if "enhancement" in lowered or "feature" in lowered:
        return "feature"
    if FEATURE.search(title):
        return "feature"
    return "bug" if BUG.search(title) else "other"


def result(verdicts: list[dict]) -> str:
    """One line for the board: the PR to merge, or what partly helps, or that nothing does."""
    fixes = sorted(v["pr"] for v in verdicts if v["verdict"] == "fixes")
    clean = sorted(v["pr"] for v in verdicts if v["verdict"] == "fixes" and v.get("merge") == "clean")
    partly = sorted(v["pr"] for v in verdicts if v["verdict"] == "partly")
    if clean:
        return f"merge #{clean[0]}"
    parts = []
    if fixes:
        parts.append("fixes, conflicts: " + ", ".join(f"#{n}" for n in fixes))
    if partly:
        parts.append("partly: " + ", ".join(f"#{n}" for n in partly))
    return " · ".join(parts) or "no open PR fixes it"


def rows(conn, repo: str) -> list[dict]:
    """Every open issue with its latest visible investigation (hidden runs are skipped)."""
    issues = conn.execute(
        """SELECT i.number, i.title, i.labels, inv.id, inv.status
           FROM issues i
           LEFT JOIN LATERAL (
               SELECT id, status FROM investigations
               WHERE repo = i.repo AND issue = i.number AND NOT hidden
               ORDER BY id DESC LIMIT 1) inv ON true
           WHERE i.repo = %s
           ORDER BY i.number DESC""", (repo,)).fetchall()
    out = []
    for number, title, labels, inv_id, status in issues:
        res = ""
        if inv_id and status in ("verified", "needs_human"):
            verdicts = [dict(zip(("pr", "verdict", "merge"), r)) for r in conn.execute(
                "SELECT pr, verdict, merge FROM pr_verdicts WHERE investigation_id = %s", (inv_id,))]
            res = result(verdicts)
        out.append({"number": number, "title": title, "kind": kind(title, labels), "status": status,
                    "inv": inv_id, "result": res})
    return out


def counts(board: list[dict]) -> dict:
    return {"open": len(board),
            "bugs": sum(r["kind"] == "bug" for r in board),
            "done": sum(r["status"] is not None for r in board),
            "ready": sum(r["result"].startswith("merge #") for r in board)}


def render_table(board: list[dict]) -> str:
    lines = [f"  {'#':<5} {'kind':<8} {'':<3} {'title':<58} result"]
    for r in board:
        title = r["title"] if len(r["title"]) <= 56 else r["title"][:55] + "…"
        lines.append(f"  {r['number']:<5} {r['kind']:<8} {SYMBOL[r['status']]:<3} {title:<58} {r['result'] or '—'}")
    c = counts(board)
    lines.append(f"\n  {c['open']} open issue{'s' * (c['open'] != 1)} · {c['bugs']} bug{'s' * (c['bugs'] != 1)} · "
                 f"{c['done']} investigated · {c['ready']} ready to merge")
    return "\n".join(lines)


def render_html(repo: str, board: list[dict], open_prs: int | None = None) -> str:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
    return env.get_template("board.html.j2").render(
        repo=repo, rows=board, counts=counts(board), open_prs=open_prs, symbol=SYMBOL,
        generated=date.today().isoformat())


def write(conn, repo: str) -> Path:
    """Write site/<name>/index.html and one packet page per investigated issue; return the board path."""
    out = SITE_DIR / repo.split("/")[1]
    (out / "issues").mkdir(parents=True, exist_ok=True)
    board = rows(conn, repo)
    for r in board:
        if r["inv"] and r["status"] in ("verified", "needs_human"):
            source = packet.OUT_DIR / f"packet-{r['inv']}.html"
            if not source.exists():
                packet.write(conn, r["inv"])
            shutil.copyfile(source, out / "issues" / f"{r['number']}.html")
    open_prs = conn.execute("SELECT count(*) FROM prs WHERE repo = %s", (repo,)).fetchone()[0]
    (out / "index.html").write_text(render_html(repo, board, open_prs))
    return out / "index.html"
