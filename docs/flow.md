# Execution flow

How a run travels through the code, file by file. Status as of 2026-10-10: only step 01 (`gate/probe.py`) exists. Everything marked **planned** is in PLAN.md but not written yet, so it is described as designed, not as built.

## The whole path (V1, as designed)

```
gh issue view ──► out/issue-<n>.json ─┐
                                      ▼
gh api (issues, PRs, PR files) ──► Postgres `escalation_gate`        [planned · step 02 · gate/collect.py, gate/db.py]
                                      │
                                      ▼
investigate: claude -p, read-only, in vendor/<repo>  ──► JSON {cause, repro, evidence[], confidence}
                                      │                       [built as a probe · step 01 · gate/probe.py]
                                      ▼
check: each evidence file:line:quote really exists     [planned · step 03 · gate/check.py]
                                      │  fail → one retry with the failures → still fails → needs_human
                                      ▼
candidate PRs: SQL over pr_files                        [planned · step 05 · sql/ + gate/candidates.py]
                                      │
                                      ▼
packet: Markdown + HTML page in out/                    [planned · step 06 · gate/packet.py]
```

Entry point for the finished tool: `python -m gate.cli packet --repo owner/name --issue N` (**planned**, step 06/07). Today the only entry point is `uv run python -m gate.probe`.

## Boundaries: where the code leaves Python

| Boundary | Crossed by | What crosses it | What can go wrong there |
|---|---|---|---|
| **GitHub (network)** | the `gh` CLI, called with `subprocess` | Issue text in. Later: issues, PRs, PR file diffs | Not logged in, rate limit, wrong repo name, offline |
| **Claude Code (separate process + Anthropic's servers)** | `claude -p`, called with `subprocess.run` in `gate/probe.py:67` | Prompt in on stdin. One JSON "envelope" out on stdout | Timeout (6 min), usage limit, a wrong citation in a valid format |
| **The target repo on disk** | Claude's Read/Grep/Glob tools, inside `vendor/hiring-agent` (`cwd=REPO_DIR`, `probe.py:70`) | Code is read. Nothing is written: Bash, Edit, Write and web tools are blocked | The copy goes stale against upstream, so we pin it at `e0ece80` |
| **Postgres (separate server process)** | `psycopg`, at `DATABASE_URL` | Rows in and out | Server not running, database not created (**planned**) |

There is no auth, tenant or web layer. Nothing is ever posted to GitHub.

## Step 01: `gate/probe.py`, traced line by line

1. `probe.py:8–12`: the paths are worked out from the file's own location. `ROOT` is the `escalation-gate/` folder, `REPO_DIR` is `vendor/hiring-agent`, input is `out/issue-424.json`, output is `out/probe-424.json`, and the limit is 360 s.
2. `probe.py:51`: `main()` reads the issue JSON (made earlier by `gh issue view 424 … > out/issue-424.json`).
3. `probe.py:37` + `.format(...)`: the issue's number, title and body are filled into `PROMPT_TEMPLATE`.
4. `probe.py:58`: `cmd` is built: `claude -p`, JSON output, `--json-schema` (from `SCHEMA`, `probe.py:14`), Read/Grep/Glob allowed, Bash/Edit/Write/WebFetch/WebSearch blocked.
5. `probe.py:67`: `subprocess.run` starts Claude Code inside `REPO_DIR`, sends the prompt on stdin, and waits for at most `TIMEOUT_S`. **This is where control leaves our code.** Claude reads files in the target repo for about 12 turns and about 50 s.
6. Three ways out:
   - `probe.py:75` timeout: print to stderr, exit 1. **Everything Claude did is lost** (see findings: step 04 will use `--session-id` + stream-json).
   - `probe.py:79` Claude failed: print its stderr, exit with its code.
   - success: `probe.py:83` parses the envelope, `:84` saves it whole to `out/probe-424.json`, and `:86` prints `structured_output` (the schema-shaped answer).
7. Nothing checks whether the answer is true. A made-up line number passes straight through. Step 03 closes that gap.

## Step 02: collect (planned, code given in chat 2026-10-10, not yet typed)

`python -m gate.collect owner/name`, then:

1. `gate/db.py` `connect()`: open Postgres at `DATABASE_URL`, create the `issues`, `prs` and `pr_files` tables if they're missing.
2. `gh issue list`: upsert into `issues`, and delete rows for issues no longer open.
3. `gh pr list`: upsert into `prs`, and delete closed PRs (their `pr_files` rows go too, through `ON DELETE CASCADE`).
4. Then one `gh api repos/…/pulls/N/files` call per PR: replace that PR's rows in `pr_files`, committing after each PR.

## What this session touched (2026-10-10)

- Built: `pyproject.toml`, `gate/__init__.py`, `gate/probe.py`, `vendor/hiring-agent` (a local clone at `e0ece80`), `out/issue-424.json`, `out/probe-424.json`.
- Designed, not yet written: `gate/db.py`, `gate/collect.py` (Postgres).
- Docs: `docs/decisions.md` (Postgres entry), `PLAN.md` (status, step 04 "Done" condition, step 05 limit), `findings.md`, superseded banners on both explainer pages, this file.

## Known gaps in this trace

- `investigate.py`, `check.py`, `candidates.py`, `packet.py` and `cli.py` don't exist. Their part of the path is the plan, not traced code.
- How `claude -p` chooses which files to read is inside Claude Code, which we can't trace. We only see its output (and, from step 04, its stream of events).
