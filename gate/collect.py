"""Step 02: copy one repo's open issues, open PRs and PR files from GitHub into Postgres."""
import json
import subprocess
import sys

from gate.db import connect

def gh(*args: str) -> str:
    """Run a GitHub CLI command and return what it printed; stop with its error if it fails."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"gh {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout

def collect(repo: str) -> None:
    conn = connect()
    cur = conn.cursor()

    issues = json.loads(gh("issue", "list", "-R", repo, "--state", "open", "--limit", "1000", "--json", "number,title,body,labels"))
    cur.executemany(
        """
        INSERT INTO issues (repo, number, title, body, labels)VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (repo, number) DO UPDATE SET title = EXCLUDED.title, body = EXCLUDED.body, labels = EXCLUDED.labels
        """,
        [(repo, i["number"], i["title"], i["body"] or "", [label["name"] for label in i["labels"]]) for i in issues]
    )
    cur.execute("DELETE FROM issues WHERE repo = %s AND NOT (number=ANY(%s::int[]))", (repo, [i["number"] for i in issues]))

    prs = json.loads(gh("pr", "list", "-R", repo, "--state", "open", "--limit", "1000", "--json", "number,title,body,author,createdAt"))
    cur.executemany(
        """
        INSERT INTO prs (repo, number, title, body, author, created_at) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (repo, number) DO UPDATE SET title = EXCLUDED.title, body = EXCLUDED.body
        """,
        [(repo, pr["number"], pr["title"], pr["body"] or "", (pr["author"] or {}).get("login", "ghost"), pr["createdAt"]) for pr in prs]
    )
    cur.execute("DELETE FROM prs WHERE repo = %s AND NOT (number=ANY(%s::int[]))", (repo, [pr["number"] for pr in prs]))
    conn.commit()

    print(f"{len(issues)} issues and {len(prs)} PRs collected for {repo}.")

    for k, p in enumerate(prs, 1):
        lines = gh("api", f"repos/{repo}/pulls/{p['number']}/files", "--paginate", "--jq", ".[] | {filename, status, additions, deletions, patch}")
        files = [json.loads(line) for line in lines.splitlines() if line.strip()]
        cur.execute("DELETE FROM pr_files WHERE repo = %s AND pr = %s", (repo, p["number"]))
        cur.executemany(
            """
            INSERT INTO pr_files (repo, pr, filename, status, additions, deletions, patch) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [(repo, p["number"], f["filename"], f["status"], f["additions"], f["deletions"], f["patch"] or "") for f in files]
        )
        conn.commit()
        print(f"[{k}/{len(prs)}] PR #{p['number']}: {len(files)} files")
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m gate.collect owner/name")
    collect(sys.argv[1])