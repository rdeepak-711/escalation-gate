"""Step 07: one command from a repo and an issue (or symptom) to a packet: clone, collect, investigate, judge, write."""
import argparse
import subprocess
import sys
from pathlib import Path

from gate import board, candidates, investigate, packet, sweep
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
    target.add_argument("--sweep", action="store_true", help="investigate the highest-ranked open issues")
    parser.add_argument("--model", default="sonnet", help="Claude model for both Claude steps (default: sonnet)")
    parser.add_argument("--refresh", action="store_true", help="collect issues and PRs again even if already stored")
    parser.add_argument("--top", type=int, help="number of top-ranked non-feature issues (1–100)")
    parser.add_argument("--issues", help="comma-separated issue numbers to sweep")
    parser.add_argument("--sweep-id", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if (args.top is not None or args.issues is not None or args.sweep_id is not None) and not args.sweep:
        parser.error("--top, --issues and --sweep-id require --sweep")
    if args.sweep and args.top is not None and not 1 <= args.top <= 100:
        parser.error("--top must be between 1 and 100")
    if args.issues is not None:
        try:
            issue_numbers = [int(n) for n in args.issues.split(",")]
            if not issue_numbers or any(n <= 0 for n in issue_numbers):
                raise ValueError
        except ValueError:
            parser.error("--issues must be comma-separated positive integers")
    else:
        issue_numbers = None

    path = VENDOR / args.repo.split("/")[1]
    if args.sweep:
        conn = None
        top = None if issue_numbers is not None else (args.top if args.top is not None else 10)
        sweep_id = args.sweep_id
        try:
            conn = connect()
            if not path.exists():
                clone(args.repo, path)
            collect(args.repo)
            if sweep_id is None:
                sweep_id = sweep.create(conn, args.repo, top, issue_numbers, args.model)
            selected = sweep.plan(conn, args.repo, top, issue_numbers)
            sweep.run(conn, args.repo, sweep_id, selected, args.model)
            print(f"\nboard: open {board.write(conn, args.repo)}")
            conn.close()
            return 0
        except (Exception, SystemExit) as exc:
            if conn is not None:
                try:
                    conn.rollback()
                    if sweep_id is not None:
                        sweep.finish(conn, sweep_id, "error")
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            print(f"sweep failed: {exc}", file=sys.stderr)
            return 1
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

    if args.issue is not None:
        status = sweep.process_issue(conn, args.repo, args.issue, args.model)
        inv_id = conn.execute(
            "SELECT id FROM investigations WHERE repo = %s AND issue = %s ORDER BY id DESC LIMIT 1",
            (args.repo, args.issue),
        ).fetchone()[0]
        html = packet.OUT_DIR / f"packet-{inv_id}.html"
    else:
        inv_id, outcome = investigate.run(conn, args.repo, args.issue, args.symptom, args.model)
        status = outcome["status"]
    if status not in ("verified", "needs_human"):
        conn.close()
        print(f"stopped: investigation {inv_id} ended '{status}', so there is nothing to judge PRs against")
        return 1

    if args.issue is None:
        candidates.run(conn, inv_id, args.model)
        html = packet.write(conn, inv_id)
    page = board.write(conn, args.repo)
    conn.close()
    print(f"\npacket ready: open {html}\nboard updated: open {page}")
    return 0 if status == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
