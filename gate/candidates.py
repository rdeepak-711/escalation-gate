"""Step 05: shortlist open PRs that touch the cited lines or mention the issue, then let Claude judge each one."""
import argparse
import json
import re
import sys
import uuid

from gate.db import connect
from gate.investigate import ROOT, RUNS_DIR, run_claude

MAX_CANDIDATES = 30
PATCH_LIMIT = 12_000
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", re.M)

VERDICT_SCHEMA = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["pr", "verdict", "why"],
                "properties": {
                    "pr": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["fixes", "partly", "no"]},
                    "why": {"type": "string"},
                },
            },
        },
    },
}

PROMPT_TEMPLATE = """A bug has been investigated and its root cause confirmed in the code:

CAUSE: {cause}

EVIDENCE:
{evidence}

Below are open pull requests that change the cited code or mention the issue. For each one, decide from its diff:
- "fixes": once merged, the root cause above is gone.
- "partly": it removes part of the cause, or only on some code paths.
- "no": it does not address the cause, even if it changes the same files.
Give one verdict per pull request, and say in one sentence what in the diff decided it.

{prs}"""

RETRY_TEMPLATE = """Your verdicts did not match the list of pull requests:

{problems}

Answer again with exactly one verdict for each pull request listed earlier."""


def hunk_ranges(patch: str) -> list[tuple[int, int]]:
    """The base-file line range each hunk of a diff replaces, as (first, last)."""
    ranges = []
    for m in HUNK.finditer(patch):
        start, length = int(m.group(1)), int(m.group(2) or 1)
        ranges.append((start, start + max(length, 1) - 1))
    return ranges


def lines_hit(patch: str, lines: list[int]) -> list[int]:
    """The cited lines that fall inside a hunk of this diff."""
    ranges = hunk_ranges(patch)
    return [n for n in lines if any(first <= n <= last for first, last in ranges)]


def cited_files(report: dict) -> dict[str, list[int]]:
    """Group the evidence lines by file."""
    files: dict[str, list[int]] = {}
    for item in report["evidence"]:
        files.setdefault(item["file"], []).append(item["line"])
    return files


def rank(rows: list[tuple[int, str, str]], mentioned: set[int], cited: dict[str, list[int]]) -> list[dict]:
    """Turn (pr, filename, patch) rows into a shortlist: issue mentions first, then most cited lines changed."""
    hits: dict[int, list[str]] = {}
    for pr, filename, patch in rows:
        for line in lines_hit(patch, cited.get(filename, [])):
            hits.setdefault(pr, []).append(f"changes {filename}:{line}")

    shortlist = []
    for pr in set(hits) | mentioned:
        reasons = (["mentions the issue"] if pr in mentioned else []) + sorted(hits.get(pr, []))
        shortlist.append({"pr": pr, "reasons": reasons})
    shortlist.sort(key=lambda c: (c["pr"] not in mentioned, -len(hits.get(c["pr"], [])), c["pr"]))
    return shortlist


def check_verdicts(verdicts: list[dict], expected: list[int]) -> list[str]:
    """Every shortlisted PR judged exactly once, and nothing else."""
    problems, seen = [], set()
    for v in verdicts:
        if v["pr"] in seen:
            problems.append(f"PR #{v['pr']} judged more than once")
        elif v["pr"] not in expected:
            problems.append(f"PR #{v['pr']} was not on the shortlist")
        seen.add(v["pr"])
    problems += [f"PR #{pr} has no verdict" for pr in expected if pr not in seen]
    return problems


def clip(text: str, limit: int = PATCH_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... {len(text) - limit} more characters cut]"


def shortlist(conn, repo: str, issue: int | None, report: dict) -> list[dict]:
    """Query the collected PRs and rank them against the citations."""
    cited = cited_files(report)
    rows = conn.execute(
        "SELECT pr, filename, patch FROM pr_files WHERE repo = %s AND filename = ANY(%s)",
        (repo, list(cited)),
    ).fetchall()
    mentioned = set()
    if issue:
        pattern = f"%#{issue}%"
        mentioned = {r[0] for r in conn.execute(
            "SELECT number FROM prs WHERE repo = %s AND (title ILIKE %s OR body ILIKE %s)",
            (repo, pattern, pattern),
        )}
    return rank(rows, mentioned, cited)


def build_prompt(conn, repo: str, report: dict, candidates: list[dict]) -> str:
    cited = cited_files(report)
    evidence = "\n".join(f"- {e['file']}:{e['line']}: {e['why']}" for e in report["evidence"])
    blocks = []
    for c in candidates:
        title, body = conn.execute("SELECT title, body FROM prs WHERE repo = %s AND number = %s",
                                   (repo, c["pr"])).fetchone()
        files = conn.execute("SELECT filename, patch FROM pr_files WHERE repo = %s AND pr = %s ORDER BY filename",
                             (repo, c["pr"])).fetchall()
        diffs = "\n".join(f"--- {name}\n{clip(patch)}" for name, patch in files if name in cited)
        others = [name for name, _ in files if name not in cited]
        blocks.append(
            f"=== PR #{c['pr']}: {title}\n"
            f"Description: {clip(body, 1500) or '(none)'}\n"
            f"Other files changed (diff not shown): {', '.join(others) or 'none'}\n"
            f"{diffs or '(no diff of the cited files)'}"
        )
    return PROMPT_TEMPLATE.format(cause=report["cause"], evidence=evidence, prs="\n\n".join(blocks))


def judge(prompt: str, expected: list[int], model: str | None = None) -> tuple[list[dict], list[str], list[float]]:
    """Ask for verdicts with no tools (the diffs are in the prompt); retry once if the list doesn't match."""
    session_id = str(uuid.uuid4())
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log = RUNS_DIR / f"{session_id}.jsonl"
    verdicts, problems, costs = [], [], []
    for attempt in (1, 2):
        result = run_claude(prompt, ROOT, session_id, resume=attempt > 1, log=log, model=model,
                            schema=VERDICT_SCHEMA, tools="")
        if result is None:
            return verdicts, ["timed out"], costs
        costs.append(result.get("total_cost_usd", 0.0))
        verdicts = (result.get("structured_output") or {}).get("verdicts", [])
        problems = check_verdicts(verdicts, expected)
        if not problems:
            break
        prompt = RETRY_TEMPLATE.format(problems="\n".join(problems))
    return verdicts, problems, costs


def main() -> int:
    parser = argparse.ArgumentParser(description="Judge the open PRs against one investigation.")
    parser.add_argument("--investigation", type=int, required=True, help="id in the investigations table")
    parser.add_argument("--model", help="e.g. sonnet or opus")
    args = parser.parse_args()

    conn = connect()
    row = conn.execute("SELECT repo, issue, status, report FROM investigations WHERE id = %s",
                       (args.investigation,)).fetchone()
    if row is None or row[3] is None:
        sys.exit(f"investigation {args.investigation} has no report")
    repo, issue, status, report = row
    if status != "verified":
        print(f"warning: investigation {args.investigation} is '{status}', so its citations are not all checked")

    candidates = shortlist(conn, repo, issue, report)
    if len(candidates) > MAX_CANDIDATES:
        print(f"{len(candidates)} candidates; judging the top {MAX_CANDIDATES}")
        candidates = candidates[:MAX_CANDIDATES]
    if not candidates:
        print("no open PR touches the cited lines or mentions the issue")
        return 0

    expected = [c["pr"] for c in candidates]
    verdicts, problems, costs = judge(build_prompt(conn, repo, report, candidates), expected, args.model)

    reasons = {c["pr"]: c["reasons"] for c in candidates}
    conn.execute("DELETE FROM pr_verdicts WHERE investigation_id = %s", (args.investigation,))
    for v in verdicts:
        if v["pr"] in reasons:
            conn.execute(
                """INSERT INTO pr_verdicts (investigation_id, pr, reasons, verdict, why)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (args.investigation, v["pr"], reasons[v["pr"]], v["verdict"], v["why"]),
            )
    conn.commit()
    conn.close()

    order = {"fixes": 0, "partly": 1, "no": 2}
    print(f"{len(candidates)} PRs judged, cost ${sum(costs):.2f}")
    for v in sorted(verdicts, key=lambda v: (order[v["verdict"]], v["pr"])):
        print(f"#{v['pr']:<5} {v['verdict']:<7} {v['why']}")
    for p in problems:
        print("FAILED:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
