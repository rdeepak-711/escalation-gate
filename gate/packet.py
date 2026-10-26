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
from gate.effects import KIND_LABEL
from gate.investigate import ROOT

OUT_DIR = ROOT / "out"
ORDER = {"fixes": 0, "partly": 1, "no": 2}
MERGE_LABEL = {"clean": "merges clean", "conflicts": "conflicts", "unknown": "unknown", None: "not checked"}


def confidence(inv_status: str, verdict: dict) -> dict | None:
    """Describe confidence using only the cause, fix lines, and merge checks that ran."""
    if verdict["verdict"] == "no":
        return None
    cause_checked = inv_status == "verified"
    lines = verdict.get("fix_lines", [])
    checked = sum(bool(line.get("checked")) for line in lines)
    if not lines:
        fix_check = "no fix lines given"
    elif checked == len(lines):
        fix_check = "fix lines checked"
    else:
        fix_check = f"{checked} of {len(lines)} fix lines checked"
    merge = verdict.get("merge")
    merge_check = {"clean": "merges clean", "conflicts": "conflicts"}.get(merge, "merge not checked")
    effects = verdict.get("side_effects")
    found = sum(bool(e.get("checked")) for e in effects) if effects is not None else 0
    if effects is None:
        effects_check = "side effects not checked"
    elif found == 0:
        effects_check = "no side effects found"
    else:
        effects_check = f"{found} side effect{'s' * (found != 1)} found"
    checks = ["cause lines checked" if cause_checked else "some cause lines failed the check",
              fix_check, merge_check, effects_check]
    if verdict.get("draft"):
        checks.append("draft PR")
    if not cause_checked or (lines and checked == 0):
        level = "low"
    elif (cause_checked and verdict["verdict"] == "fixes" and lines and checked == len(lines)
          and merge == "clean" and effects_check == "no side effects found" and not verdict.get("draft")):
        level = "high"
    else:
        level = "medium"
    return {"level": level, "checks": checks, "text": f"{level}: " + " · ".join(checks)}


def order(verdicts: list[dict]) -> list[dict]:
    return sorted(verdicts, key=lambda v: (ORDER[v["verdict"]], v["pr"]))


def recommend(verdicts: list[dict]) -> dict | None:
    """The first PR judged to fix the cause that also merges clean and isn't a draft."""
    return next((v for v in order(verdicts)
                 if v["verdict"] == "fixes" and v["merge"] == "clean" and not v.get("draft")), None)


def overlap_groups(verdicts: list[dict]) -> list[list[int]]:
    """PRs grouped by shared changed files (transitively), in order: only PRs in one group can duplicate each other."""
    groups: list[tuple[set[str], list[int]]] = []
    for v in verdicts:
        files, prs = set(v.get("files", [])), [v["pr"]]
        for group in [g for g in groups if g[0] & files]:
            groups.remove(group)
            files |= group[0]
            prs = group[1] + prs
        groups.append((files, prs))
    found = [sorted(prs, key=[x["pr"] for x in verdicts].index) for _, prs in groups]
    return sorted(found, key=lambda g: [x["pr"] for x in verdicts].index(g[0]))


def overlap_note(groups: list[list[int]]) -> str:
    """One line on whether the PRs that change the cause overlap, from their changed files, not from the model."""
    if len(groups) == 1:
        return (f"{joined(groups[0])} all change the cause and change the same files. "
                "Once one is merged, the others likely duplicate it.")
    if all(len(g) == 1 for g in groups):
        return (f"{joined([g[0] for g in groups])} change different files, "
                "so they may be needed together rather than one instead of another.")
    parts = "; ".join(joined(g) for g in groups)
    return (f"These groups change different files from each other: {parts}. Within a group, once one is merged "
            "the others likely duplicate it; across groups they may be needed together.")


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
    env.globals.update(merge_label=MERGE_LABEL, kind_label=KIND_LABEL)
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
    for verdict in verdicts:
        verdict["confidence"] = confidence(data["status"], verdict)
    lead, rest = split_cause(data["report"]["cause"])
    return {**data, "verdicts": verdicts, "recommended": recommend(verdicts),
            "cause_lead": lead, "cause_rest": rest, "label": label(data["title"]),
            "judged": [v for v in verdicts if v["verdict"] != "no"],
            "rejected": [v for v in verdicts if v["verdict"] == "no"],
            "files": group_by_file(data["report"]["evidence"]), "board": board(verdicts),
            "chain": chain(data["report"]), "open_prs": data.get("open_prs"),
            "verified": sum(bool(e.get("checked")) for e in data["report"]["evidence"]),
            "competing": [v["pr"] for v in verdicts if v["verdict"] != "no"],
            "overlap": overlap_note(overlap_groups([v for v in verdicts if v["verdict"] != "no"]))
            if sum(v["verdict"] != "no" for v in verdicts) > 1 else "",
            "draft_fixes": [v for v in verdicts if v["verdict"] == "fixes" and v.get("draft")],
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

    verdicts = [dict(zip(("pr", "verdict", "why", "reasons", "title", "author", "draft", "files", "fix_lines",
                          "side_effects"), r)) for r in conn.execute(
        """SELECT v.pr, v.verdict, v.why, v.reasons, p.title, p.author, p.draft,
                  ARRAY(SELECT f.filename FROM pr_files f WHERE f.repo = p.repo AND f.pr = p.number)
                  , v.fix_lines, v.side_effects
           FROM pr_verdicts v JOIN prs p ON p.repo = %s AND p.number = v.pr
           WHERE v.investigation_id = %s""", (repo, inv_id))]
    worth_merging = [v["pr"] for v in verdicts if v["verdict"] != "no"]
    fetched = bool(worth_merging) and fetch_prs(repo_dir, repo, worth_merging)
    for v in verdicts:
        if v["verdict"] == "no":
            v["merge"] = None
        else:
            v["merge"] = merge_status(repo_dir, f"refs/gate/pr/{v['pr']}") if fetched else "unknown"
            conn.execute("UPDATE pr_verdicts SET merge = %s WHERE investigation_id = %s AND pr = %s",
                         (v["merge"], inv_id, v["pr"]))
    conn.commit()

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
