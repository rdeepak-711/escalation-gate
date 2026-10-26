"""Step 05b: the side-effect pass. After the judge, one Claude call per issue asks what else each fixing PR changes,
and every claim is checked against the diff on the side it names (old line numbers for removed lines, new for added)."""
import argparse
import json
import re
import sys
import uuid

from gate import candidates
from gate.db import connect
from gate.investigate import ROOT, RUNS_DIR, run_claude

PATCH_LIMIT = 12_000
KINDS = ["removed_name", "deleted_data", "changed_default", "new_undefined_name", "other"]
KIND_LABEL = {"removed_name": "removed name", "deleted_data": "deleted data", "changed_default": "changed default",
              "new_undefined_name": "new undefined name", "other": "other"}

EFFECTS_SCHEMA = {
    "type": "object",
    "required": ["effects"],
    "properties": {
        "effects": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["pr", "kind", "file", "line", "side", "quote", "why"],
                "properties": {
                    "pr": {"type": "integer"},
                    "kind": {"type": "string", "enum": KINDS},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "side": {"type": "string", "enum": ["added", "removed"]},
                    "quote": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
    },
}

PROMPT_TEMPLATE = """Each pull request below was judged to fix a bug. For each one, list what else it changes beyond the fix:
- names it removes or renames (a function, class, variable, setting or field that other code may still use)
- data it deletes or stops collecting
- defaults it changes
- names it uses that are not defined anywhere in the diff or the surrounding code

For each such change give: the pr number; the kind (removed_name, deleted_data, changed_default, new_undefined_name
or other); the file; the line number, which is the line in the old version of the file for a removed line and the
line in the new version of the file for an added line; the side (added or removed); the exact text of that line
without the leading + or -; and why it matters to someone merging the pull request. Only report lines that are really
in the diff. Return an empty list if a pull request changes nothing beyond the fix.

{prs}"""


def removed_lines(patch: str) -> dict[int, str]:
    """Map old-file line numbers to removed text in a unified diff (the mirror of candidates.added_lines)."""
    removed = {}
    old_line = None
    for line in patch.splitlines():
        if line.startswith("@@"):
            match = re.search(r"-(\d+)(?:,\d+)?", line)
            old_line = int(match.group(1)) if match else None
        elif old_line is not None and line.startswith("-"):
            removed[old_line] = line[1:]
            old_line += 1
        elif old_line is not None and line.startswith(" "):
            old_line += 1
        elif line.startswith("\\ No newline at end of file"):
            continue
    return removed


def check_effects(patches: dict[str, str], effects: list[dict], slack: int = 3) -> list[dict]:
    """Check each claimed effect against the PR patches on the side it names; same quote rules as fix lines."""
    checked = []
    for item in effects:
        quote = (item.get("quote") or "").strip()
        forms = {f for f in (quote, quote.removeprefix("+").strip(), quote.removeprefix("-").strip()) if len(f) >= 4}
        patch = patches.get(item.get("file")) or ""
        side = item.get("side")
        if side == "added":
            patch_lines = candidates.added_lines(patch)
        elif side == "removed":
            patch_lines = removed_lines(patch)
        else:
            patch_lines = {}
        line = item.get("line")
        valid = isinstance(line, int) and any(abs(n - line) <= slack and f in text
                                              for n, text in patch_lines.items() for f in forms)
        checked.append({**item, "checked": valid})
    return checked


def build_prompt(conn, repo: str, verdicts: list[dict]) -> str:
    """Every file of each fixing PR, one block per PR, each block clipped at PATCH_LIMIT."""
    blocks = []
    for v in verdicts:
        title = conn.execute("SELECT title FROM prs WHERE repo = %s AND number = %s", (repo, v["pr"])).fetchone()
        files = conn.execute("SELECT filename, patch FROM pr_files WHERE repo = %s AND pr = %s ORDER BY filename",
                             (repo, v["pr"])).fetchall()
        diffs = "\n".join(f"--- {name}\n{patch}" for name, patch in files)
        blocks.append(candidates.clip(f"=== PR #{v['pr']}: {title[0] if title else ''}\n{diffs or '(no diff)'}",
                                      PATCH_LIMIT))
    return PROMPT_TEMPLATE.format(prs="\n\n".join(blocks))


def ask(prompt: str, expected: list[int], model: str | None = None) -> tuple[list[dict], float] | None:
    """One call with no tools (the diffs are in the prompt), no retry; None on timeout or no structured output."""
    session_id = str(uuid.uuid4())
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log = RUNS_DIR / f"{session_id}.jsonl"
    result = run_claude(prompt, ROOT, session_id, resume=False, log=log, model=model, schema=EFFECTS_SCHEMA, tools="")
    if not isinstance(result, dict) or not result.get("structured_output"):
        return None
    return result["structured_output"].get("effects", []), result.get("total_cost_usd", 0.0)


def run(conn, inv_id: int, model: str | None = None) -> list[str]:
    """Ask what else each fixes/partly PR changes, check the claims against the diffs, store them; return problems."""
    row = conn.execute("SELECT repo FROM investigations WHERE id = %s", (inv_id,)).fetchone()
    if row is None:
        sys.exit(f"no investigation {inv_id}")
    repo = row[0]
    verdicts = [{"pr": pr, "verdict": verdict} for pr, verdict in conn.execute(
        "SELECT pr, verdict FROM pr_verdicts WHERE investigation_id = %s AND verdict IN ('fixes', 'partly') ORDER BY pr",
        (inv_id,))]
    if not verdicts:
        return []

    expected = [v["pr"] for v in verdicts]
    answer = ask(build_prompt(conn, repo, verdicts), expected, model)
    if answer is None:
        print("FAILED: side effects not checked")
        return ["side effects not checked"]
    effects, cost = answer

    total = 0
    for pr in expected:
        patches = dict(conn.execute("SELECT filename, patch FROM pr_files WHERE repo = %s AND pr = %s", (repo, pr)))
        checked = check_effects(patches, [e for e in effects if e.get("pr") == pr])
        conn.execute("UPDATE pr_verdicts SET side_effects = %s::jsonb WHERE investigation_id = %s AND pr = %s",
                     (json.dumps(checked), inv_id, pr))
        for e in checked:
            total += bool(e["checked"])
            print(f"#{pr:<5} {'✅' if e['checked'] else '❌'} {KIND_LABEL.get(e.get('kind'), e.get('kind'))}: "
                  f"{e.get('file')}:{e.get('line')} {e.get('why')}")
    conn.execute("UPDATE investigations SET effects_cost_usd = %s WHERE id = %s", (cost, inv_id))
    conn.commit()
    print(f"{len(expected)} PRs checked for side effects, {total} found, cost ${cost:.2f}")
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask what else the fixing PRs of one investigation change.")
    parser.add_argument("--investigation", type=int, required=True, help="id in the investigations table")
    parser.add_argument("--model", help="e.g. sonnet or opus")
    args = parser.parse_args()

    conn = connect()
    problems = run(conn, args.investigation, args.model)
    conn.close()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
