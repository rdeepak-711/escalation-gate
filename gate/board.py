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
    drafts = sorted(v["pr"] for v in verdicts if v["verdict"] == "fixes" and v.get("draft"))
    partly = sorted(v["pr"] for v in verdicts if v["verdict"] == "partly")
    ready = [n for n in clean if n not in drafts]
    if ready:
        return f"merge #{ready[0]}"
    parts = []
    if drafts:
        parts.append("fixes, draft: " + ", ".join(f"#{n}" for n in drafts))
    if [n for n in fixes if n not in drafts]:
        parts.append("fixes, conflicts: " + ", ".join(f"#{n}" for n in fixes if n not in drafts))
    if partly:
        parts.append("partly: " + ", ".join(f"#{n}" for n in partly))
    return " · ".join(parts) or "no open PR fixes it"


def issue_url(repo: str, n: int) -> str:
    return f"https://github.com/{repo}/issues/{n}"


def pr_url(repo: str, n: int) -> str:
    return f"https://github.com/{repo}/pull/{n}"


def effects_count(verdict: dict) -> int | None:
    """How many side effects were checked in the diff, or None when the pass did not run for this PR."""
    effects = verdict.get("side_effects")
    return None if effects is None else sum(bool(e.get("checked")) for e in effects)


def best_pr(repo: str, inv_status: str, verdicts: list[dict]) -> dict | None:
    """Choose the packet recommendation, or the best fixes/partly fallback for display."""
    recommended = packet.recommend(verdicts)
    chosen = recommended or next((v for v in packet.order(verdicts) if v["verdict"] == "fixes"), None)
    chosen = chosen or next((v for v in packet.order(verdicts) if v["verdict"] == "partly"), None)
    if chosen is None:
        return None
    return {"pr": chosen["pr"], "url": pr_url(repo, chosen["pr"]), "verdict": chosen["verdict"],
            "why": chosen["why"], "confidence": packet.confidence(inv_status, chosen),
            "effects": effects_count(chosen)}


def other_prs(repo: str, inv_status: str, verdicts: list[dict], best: dict | None) -> list[dict]:
    """Every other PR that fixes or partly fixes the issue: once one is merged, the rest likely duplicate it."""
    if best is None:
        return []
    return [{"pr": v["pr"], "url": pr_url(repo, v["pr"]), "verdict": v["verdict"], "merge": v.get("merge"),
             "why": v["why"], "confidence": packet.confidence(inv_status, v), "effects": effects_count(v)}
            for v in packet.order(verdicts) if v["verdict"] != "no" and v["pr"] != best["pr"]]


def rows(conn, repo: str) -> list[dict]:
    """Every open issue with its latest visible investigation (hidden runs are skipped)."""
    issues = conn.execute(
        """SELECT i.number, i.title, i.labels, inv.id, inv.status, i.thumbs_up, i.comments
           FROM issues i
           LEFT JOIN LATERAL (
               SELECT id, status FROM investigations
               WHERE repo = i.repo AND issue = i.number AND NOT hidden
               ORDER BY id DESC LIMIT 1) inv ON true
           WHERE i.repo = %s
           """, (repo,)).fetchall()
    from gate.sweep import rank_issues
    prs = [dict(zip(("number", "title", "body"), r)) for r in conn.execute(
        "SELECT number, title, body FROM prs WHERE repo = %s", (repo,))]
    ranked = rank_issues([{"number": n, "title": title, "labels": labels,
                           "thumbs_up": thumbs, "comments": comments}
                          for n, title, labels, _, _, thumbs, comments in issues], prs)
    by_number = {r[0]: r for r in issues}
    out = []
    for ranked_issue in ranked:
        number = ranked_issue["number"]
        _, title, labels, inv_id, status, _, _ = by_number[number]
        res = ""
        best = None
        others = []
        if inv_id and status in ("verified", "needs_human"):
            verdicts = [dict(zip(("pr", "verdict", "merge", "draft", "why", "fix_lines", "side_effects"), r))
                        for r in conn.execute(
                """SELECT v.pr, v.verdict, v.merge, p.draft, v.why, v.fix_lines, v.side_effects FROM pr_verdicts v
                   LEFT JOIN prs p ON p.repo = %s AND p.number = v.pr
                   WHERE v.investigation_id = %s""", (repo, inv_id))]
            res = result(verdicts)
            best = best_pr(repo, status, verdicts)
            others = other_prs(repo, status, verdicts, best)
        out.append({"number": number, "title": title, "kind": kind(title, labels), "status": status,
                    "inv": inv_id, "result": res, "url": issue_url(repo, number), "best": best,
                    "others": others})
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
        generated=date.today().isoformat(), issue_url=issue_url, pr_url=pr_url,
        merge_label=packet.MERGE_LABEL)


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
