"""Step 07: one command from a repo and an issue (or symptom) to a packet: clone, collect, investigate, judge, write."""
import argparse
import subprocess
import sys
from pathlib import Path

from gate import board, candidates, investigate, packet
from gate.collect import collect
from gate.db import connect
from gate.investigate import ROOT

VENDOR = ROOT / "vendor"


def clone(repo: str, path: Path) -> None:
    """Full history (merge checks need it), file contents fetched lazily."""
    print(f"cloning {repo} into {path}")
    subprocess.run(["git", "clone", "-q", "--filter=blob:none", f"https://github.com/{repo}", str(path)], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Investigate an issue or symptom in a GitHub repo and write a packet.")
    parser.add_argument("--repo", required=True, help="owner/name on GitHub")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--issue", type=int)
    target.add_argument("--symptom")
    target.add_argument("--list", action="store_true", help="show every open issue with its status, and write the board page")
    parser.add_argument("--model", default="sonnet", help="Claude model for both Claude steps (default: sonnet)")
    parser.add_argument("--refresh", action="store_true", help="collect issues and PRs again even if already stored")
    args = parser.parse_args(argv)

    path = VENDOR / args.repo.split("/")[1]
    if not path.exists():
        clone(args.repo, path)

    conn = connect()
    collected = conn.execute("SELECT count(*) FROM prs WHERE repo = %s", (args.repo,)).fetchone()[0]
    if args.refresh or not collected:
        print(f"collecting open issues and PRs of {args.repo} (a few minutes for a busy repo)")
        collect(args.repo)

    if args.list:
        print(board.render_table(board.rows(conn, args.repo)))
        print(f"\nboard: open {board.write(conn, args.repo)}")
        conn.close()
        return 0

    inv_id, outcome = investigate.run(conn, args.repo, args.issue, args.symptom, args.model)
    if outcome["status"] not in ("verified", "needs_human"):
        conn.close()
        print(f"stopped: investigation {inv_id} ended '{outcome['status']}', so there is nothing to judge PRs against")
        return 1

    candidates.run(conn, inv_id, args.model)
    html = packet.write(conn, inv_id)
    page = board.write(conn, args.repo)
    conn.close()
    print(f"\npacket ready: open {html}\nboard updated: open {page}")
    return 0 if outcome["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
