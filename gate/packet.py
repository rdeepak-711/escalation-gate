"""Step 06: turn one investigation and its PR verdicts into a packet (Markdown + HTML) an engineer can act on."""
import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from gate.check import check
from gate.db import connect
from gate.investigate import ROOT

OUT_DIR = ROOT / "out"
ORDER = {"fixes": 0, "partly": 1, "no": 2}
MERGE_LABEL = {"clean": "merges clean", "conflicts": "conflicts", "unknown": "unknown", None: "not checked"}


def order(verdicts: list[dict]) -> list[dict]:
    return sorted(verdicts, key=lambda v: (ORDER[v["verdict"]], v["pr"]))


def recommend(verdicts: list[dict]) -> dict | None:
    """The first PR judged to fix the cause that also merges clean."""
    return next((v for v in order(verdicts) if v["verdict"] == "fixes" and v["merge"] == "clean"), None)


def joined(prs: list[int]) -> str:
    names = [f"#{n}" for n in prs]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def split_cause(cause: str, limit: int = 320) -> tuple[str, str]:
    """A short lead (whole sentences, about `limit` characters) and the rest, for a 'show more' fold."""
    sentences = re.split(r"(?<=[.!?])\s+", cause.strip())
    lead = []
    for sentence in sentences:
        if lead and len(" ".join(lead + [sentence])) > limit:
            break
        lead.append(sentence)
    return " ".join(lead), " ".join(sentences[len(lead):])


def label(title: str) -> str:
    """Short name for the sidebar: 'Issue #424' or 'Reported symptom'."""
    return title.split(":")[0] if title.startswith("Issue #") else "Reported symptom"


def cell(text) -> str:
    """Make text safe inside a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def environment(autoescape: bool) -> Environment:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=autoescape,
                      trim_blocks=True, lstrip_blocks=True)
    env.filters.update(cell=cell, joined=joined)
    env.globals.update(merge_label=MERGE_LABEL)
    return env


def group_by_file(evidence: list[dict]) -> list[dict]:
    """Citations grouped per file, in first-seen order, each keeping its evidence number."""
    groups: dict[str, list[dict]] = {}
    for n, item in enumerate(evidence, start=1):
        groups.setdefault(item["file"], []).append({**item, "n": n})
    return [{"file": f, "items": items} for f, items in groups.items()]


def board(verdicts: list[dict]) -> dict:
    """fixes/partly × clean/conflicts; unknown or unchecked merges go with conflicts (not proven clean)."""
    cells = {v: {"clean": [], "conflicts": []} for v in ("fixes", "partly")}
    for v in verdicts:
        if v["verdict"] in cells:
            cells[v["verdict"]]["clean" if v["merge"] == "clean" else "conflicts"].append(v)
    return cells


def chain(report: dict) -> list[dict]:
    """The model's cause chain, each step joined to the citation that backs it."""
    evidence = report["evidence"]
    steps = []
    for step in report.get("chain", []):
        n = step["evidence"]
        item = evidence[n - 1] if 1 <= n <= len(evidence) else None
        steps.append({"step": step["step"], "n": n,
                      "loc": f"{item['file']}:{item['line']}" if item else "missing citation",
                      "short": f"{item['file'].rsplit('/', 1)[-1]}:{item['line']}" if item else "missing citation",
                      "checked": bool(item and item.get("checked"))})
    return steps


def context(data: dict) -> dict:
    verdicts = order(data["verdicts"])
    lead, rest = split_cause(data["report"]["cause"])
    return {**data, "verdicts": verdicts, "recommended": recommend(verdicts),
            "cause_lead": lead, "cause_rest": rest, "label": label(data["title"]),
            "judged": [v for v in verdicts if v["verdict"] != "no"],
            "rejected": [v for v in verdicts if v["verdict"] == "no"],
            "files": group_by_file(data["report"]["evidence"]), "board": board(verdicts),
            "chain": chain(data["report"]), "open_prs": data.get("open_prs"),
            "verified": sum(bool(e.get("checked")) for e in data["report"]["evidence"]),
            "competing": [v["pr"] for v in verdicts if v["verdict"] != "no"],
            "counts": {k: sum(v["verdict"] == k for v in verdicts) for k in ORDER}}


def render_markdown(data: dict) -> str:
    return environment(False).get_template("packet.md.j2").render(**context(data))


def render_html(data: dict) -> str:
    return environment(True).get_template("packet.html.j2").render(**context(data))


def merge_status(repo_dir: Path, ref: str) -> str:
    """Would `ref` merge into the checked-out commit without conflicts? Nothing on disk changes."""
    exists = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                            capture_output=True)
    if exists.returncode != 0:
        return "unknown"  # merge-tree --quiet also exits 1 for a missing ref, which would read as "conflicts"
    proc = subprocess.run(["git", "-C", str(repo_dir), "merge-tree", "--write-tree", "--quiet", "HEAD", ref],
                          capture_output=True, text=True)
    return {0: "clean", 1: "conflicts"}.get(proc.returncode, "unknown")


def fetch_prs(repo_dir: Path, repo: str, prs: list[int]) -> bool:
    """Fetch each PR's head commit into refs/gate/pr/<n> of the local clone."""
    specs = [f"pull/{n}/head:refs/gate/pr/{n}" for n in prs]
    proc = subprocess.run(["git", "-C", str(repo_dir), "fetch", "-q", "--force",
                           f"https://github.com/{repo}", *specs], capture_output=True, text=True)
    if proc.returncode != 0:
        print("could not fetch PR heads, merge status unknown:", proc.stderr.strip())
    return proc.returncode == 0


def load(conn, inv_id: int, repo_dir: Path) -> dict:
    row = conn.execute(
        "SELECT repo, issue, symptom, status, report, costs_usd, created_at FROM investigations WHERE id = %s",
        (inv_id,)).fetchone()
    if row is None or row[4] is None:
        sys.exit(f"investigation {inv_id} has no report")
    repo, issue, symptom, status, report, costs, created = row

    if issue:
        found = conn.execute("SELECT title FROM issues WHERE repo = %s AND number = %s", (repo, issue)).fetchone()
        title = f"Issue #{issue}: {found[0] if found else '(closed since collection)'}"
    else:
        title = f"Reported symptom: {symptom}"

    # re-check every citation now, so the ✅/❌ marks describe the code the packet points at
    failures = check(report, repo_dir)
    for n, item in enumerate(report["evidence"], start=1):
        item["checked"] = not any(f.startswith(f"evidence {n}:") for f in failures)

    verdicts = [dict(zip(("pr", "verdict", "why", "reasons", "title", "author"), r)) for r in conn.execute(
        """SELECT v.pr, v.verdict, v.why, v.reasons, p.title, p.author
           FROM pr_verdicts v JOIN prs p ON p.repo = %s AND p.number = v.pr
           WHERE v.investigation_id = %s""", (repo, inv_id))]
    worth_merging = [v["pr"] for v in verdicts if v["verdict"] != "no"]
    fetched = bool(worth_merging) and fetch_prs(repo_dir, repo, worth_merging)
    for v in verdicts:
        if v["verdict"] == "no":
            v["merge"] = None
        else:
            v["merge"] = merge_status(repo_dir, f"refs/gate/pr/{v['pr']}") if fetched else "unknown"

    open_prs = conn.execute("SELECT count(*) FROM prs WHERE repo = %s", (repo,)).fetchone()[0]
    commit = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    return {"id": inv_id, "repo": repo, "commit": commit, "title": title, "status": status,
            "cost": sum(costs), "created": created.date().isoformat(), "report": report, "verdicts": verdicts,
            "open_prs": open_prs}


def write(conn, inv_id: int) -> Path:
    """Write out/packet-<id>.md and .html; return the HTML path."""
    repo = conn.execute("SELECT repo FROM investigations WHERE id = %s", (inv_id,)).fetchone()
    if repo is None:
        sys.exit(f"no investigation {inv_id}")
    data = load(conn, inv_id, ROOT / "vendor" / repo[0].split("/")[1])
    data["generated"] = date.today().isoformat()
    md_path = OUT_DIR / f"packet-{inv_id}.md"
    html_path = OUT_DIR / f"packet-{inv_id}.html"
    md_path.write_text(render_markdown(data))
    html_path.write_text(render_html(data))
    print(f"wrote {md_path}\nwrote {html_path}")
    return html_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the escalation packet for one investigation.")
    parser.add_argument("--investigation", type=int, required=True)
    args = parser.parse_args()

    conn = connect()
    write(conn, args.investigation)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
