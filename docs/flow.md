# Execution flow

How a run travels through the code, file by file. Single-issue entry point: `python -m gate.cli --repo R (--issue N | --symptom S) [--model sonnet] [--refresh]`; sweep entry point: `python -m gate.cli --repo R --sweep [--top N | --issues N,N]` or the local stdio MCP server.

## Sweep and MCP path

`gate.mcp_server` exposes four stdio tools. `start_sweep()` validates the repo, creates a row in Postgres, then launches `python -m gate.cli --sweep --sweep-id …` as a background process and returns the board path. `sweep_status()` reads the newest `sweeps` row and joins finished issue numbers to `board.rows()` for their one-line result; `get_board()` returns investigated rows, each with its `best` PR and its `others` (the other PRs that fix or partly fix the issue, passed through with `row.get("others", [])`), and `stop_sweep()` marks a live sweep as stopping.

The child CLI refreshes GitHub data with `gate.collect.collect()`, then `gate.sweep.plan()` ranks open issues using linked PRs, engagement and `board.kind()`. `gate.sweep.select()` then walks that ranking in order, skips features and issues already done, and stops once it has `top` issues (changed 26 Oct: done issues no longer use up places). `gate.sweep.run()` records the plan, handles one issue at a time through `process_issue()` (the same path as `--issue`), updates the board after each result, and stops between issues if asked. `gate.candidates` checks quoted PR fix lines against added diff lines; `gate.packet` describes confidence from those checks and merge status. `gate/board.py` renders the shared board and packet pages.

Where a sweep rolls back, and how a dead one is found (review round 1, 25 Oct). In `gate.sweep.run()`, when `process_issue()` raises any `Exception` or a `SystemExit` (from `investigate.run` or `collect.gh`), the loop calls `conn.rollback()` so the aborted transaction does not poison the next issue, records that issue as `"error"`, and goes on; `KeyboardInterrupt` is not caught. If the whole sweep crashes, the `--sweep` branch of `gate.cli.main()` catches `Exception` and `SystemExit`, calls `conn.rollback()`, then `sweep.finish(conn, sweep_id, "error")`; if either of those fails too, it still prints `sweep failed: <original error>` and returns 1. `gate.sweep.create()` and `gate.mcp_server.create_sweep()` commit the new row at once, because the sweep process reads it on its own connection. A sweep process that died without finishing is found by `gate.mcp_server.pid_alive()`, called from `start_sweep()` and `sweep_status()` when the newest row still says running or stopping: it first calls `os.waitpid(pid, os.WNOHANG)`, which reaps an exited child of the MCP server (a zombie would otherwise still answer `os.kill(pid, 0)`), and falls back to `os.kill(pid, 0)` for a process that is not its child. A dead pid gets `mark_error()`, so a new sweep can start.

Boundaries on this path: the MCP server speaks stdio to Claude Code; the child process uses the existing local Postgres database, GitHub CLI, Claude Code login and local repo clone. It does not run PR code or tests. The MCP server, sweep code, confidence labels and PR fix-line checks were added in this session; the cause investigation and citation checker remain unchanged.

```
gh (issues, PRs, PR files) ──► gate/collect.py ──► Postgres: issues, prs, pr_files           [step 02]
                                                        │
python -m gate.investigate --repo R --issue N           ▼  (title + body read from `issues`)
        or --symptom "…"  ──► gate/investigate.py: investigate()                              [step 04]
                                 │  run_claude(): claude -p, tools Read/Grep/Glob, cwd vendor/<repo>
                                 │  check() from gate/check.py on every citation                 [step 03]
                                 │  fail → one retry in the same session (--resume) → still fails → needs_human
                                 ▼
                          Postgres: investigations (status, report jsonb, costs) + out/runs/<session>.jsonl
                                 │
python -m gate.candidates --investigation ID                                                  [step 05]
                                 │  shortlist(): pr_files hunks that contain a cited line, or PR mentions #N
                                 │  judge(): one claude -p call, --tools "" (diffs in the prompt), verdict per PR
                                 ▼
                          Postgres: pr_verdicts (fixes / partly / no + why + reasons)
                                 │
python -m gate.packet --investigation ID                                                      [step 06]
                                 │  load(): re-runs check(), git fetch PR heads, git merge-tree per fix/partly PR
                                 ▼
                          out/packet-<ID>.md + out/packet-<ID>.html  (templates in gate/templates/)
```

## Boundaries: where the code leaves Python

| Boundary | Crossed by | What crosses it | What can go wrong there |
|---|---|---|---|
| **GitHub (network)** | `gh` CLI in `gate/collect.py` `gh()`; `git fetch` in `gate/packet.py` `fetch_prs()` | Issues, PRs, PR diffs in; PR head commits in. Nothing is ever posted. | Not logged in, rate limit, network timeout (seen once in step 02). A failed fetch marks merge status "unknown". |
| **Claude Code (separate process)** | `gate/investigate.py` `run_claude()`, used by both `investigate()` and `candidates.judge()` | Prompt in on stdin; stream-json events out into `out/runs/<session>.jsonl`; last line is the result | Timeout (360 s, run killed, log kept), no structured output, crash (RuntimeError, nothing saved) |
| **Target repo on disk** | Claude's Read/Grep/Glob in `vendor/<repo>`; `check()`; `git merge-tree` | Code read only. `merge-tree --write-tree` writes git objects but never touches the working tree. | Clone goes stale against upstream (pinned at `e0ece80`) |
| **Postgres** | `psycopg` via `gate/db.py` `connect()`, which reads `information_schema.columns` and runs `SCHEMA` only when a table or column from `COLUMNS` is missing (since 25 Oct; before, it ran the DDL on every connect) | Rows in and out | Server down; a SQL typo in `SCHEMA` crashes the first `connect()` that needs it (seen 2026-10-11); a schema statement that is not `CREATE TABLE IF NOT EXISTS` or `ADD COLUMN IF NOT EXISTS` fails at import |

## Step by step, with the real functions

0. **Board** — `gate.cli --list` → `gate/board.py`: `rows()` joins every open issue to its latest non-hidden investigation (`investigations.hidden` keeps known-bad runs off it), `result()` summarises its verdicts using the merge status `packet.load()` stored in `pr_verdicts.merge` (a draft fix shows as "fixes, draft: #n", never "merge #n"), `kind()` guesses bug/feature from labels and title, `best_pr()` picks the PR shown first, and `other_prs()` (added 26 Oct) lists every other verdict that is not "no", in `packet.order`, with its link, merge status and confidence; `rows()` puts it on each row as `others` (`[]` without verdicts) and the template renders it under the best PR. `write()` renders `gate/templates/board.html.j2` to `site/<name>/index.html` and copies each packet to `site/<name>/issues/<n>.html`. Every `--issue` run ends with `board.write()`.
0. **One command** — `gate/cli.py` `main()`: clone `vendor/<name>` if missing (`clone()`, `--filter=blob:none`, full history for merge checks); `collect()` if the repo has no PRs stored or `--refresh`; `investigate.run()`; stop if the status is `timeout`/`error`; `candidates.run()`; `packet.write()`; print the HTML path. Each stage's `main()` is now a thin wrapper around the same `run()`/`write()` function.

1. **Collect** — `gate/collect.py` `collect(repo)`: `gh issue list` → upsert `issues`, delete closed; `gh pr list` → upsert `prs`, delete closed (cascade removes their `pr_files`); per PR `gh api …/pulls/N/files` → replace its `pr_files` rows; commit per PR.
2. **Investigate** — `gate/investigate.py` `main()` reads the issue from Postgres (or takes `--symptom`), calls `investigate()`:
   - `run_claude(prompt, …, resume=False)` with a new `--session-id`.
   - `check(report, repo_dir)` (`gate/check.py`): file inside repo, exists, line in range, quote within ±5 lines (whitespace-normalised); every `chain` step must point at an existing evidence item.
   - failures → `RETRY_TEMPLATE` → `run_claude(…, resume=True)` (same session); still failing → `needs_human`. Timeout or no structured output → stop, no retry.
   - `save()` inserts one `investigations` row (`RETURNING id`).
3. **Candidates** — `gate/candidates.py` `main()`:
   - `shortlist()`: SQL pulls `pr_files` rows for the cited files and PRs whose title/body mention `#N`; `rank()` keeps a PR when `lines_hit()` finds a cited line inside a hunk's base range (`hunk_ranges()` reads `@@ -start,len`). Mentions first, then most lines hit; max 30.
   - `build_prompt()`: cause + evidence + each PR's title, description and the diffs of cited files (12,000 chars max each).
   - `judge()`: `run_claude(…, schema=VERDICT_SCHEMA, tools="")`; `check_verdicts()` demands exactly one verdict per shortlisted PR, one retry.
   - Writes `pr_verdicts` (old rows for that investigation deleted first, so re-runs are safe).
4. **Packet** — `gate/packet.py` `main()` → `load()`:
   - re-runs `check()` so every ✅/❌ reflects the code at packet time;
   - `fetch_prs()` fetches heads of fixes/partly PRs into `refs/gate/pr/<n>`; `merge_status()` verifies the ref exists, then `git merge-tree --write-tree --quiet HEAD ref` → clean (0) / conflicts (1);
   - `context()` adds `group_by_file()`, `board()`, `chain()` and the open-PR count; `render_markdown()` / `render_html()` fill `gate/templates/packet.md.j2` / `packet.html.j2` (HTML autoescaped); `recommend()` picks the first "fixes" PR that merges clean and isn't a draft (`prs.draft`, from `gh pr list --json isDraft` in `collect.py`). `overlap_groups()` groups the fixes/partly PRs by shared changed files (from `pr_files`), and `overlap_note()` turns that into the packet's one line: same files → "likely duplicate", different files → "may be needed together". Touched 18 Oct.

## What the 2026-10-11 session touched

- Steps 04 (Deepak typed it; Claude fixed the trailing comma in `db.py` and the `-"--resume"` bug), 05 and 06 (Claude wrote them after Deepak's "go").
- `run_claude()` gained `schema` and `tools` parameters and now passes `--tools` instead of `--allowedTools`.
- New tables `investigations`, `pr_verdicts`; new modules `gate/candidates.py`, `gate/packet.py`, `gate/templates/`.
- `docs/packets/424-score-swing.md` corrected (#415 fixes, #242 partly).

## Known gaps in this trace

- How `claude -p` decides which files to read happens inside Claude Code; we only see its event log.
- A `RuntimeError` from `run_claude()` (Claude crashed) stops the program before `save()`, so no row records it.
- `gate.cli` treats any stored PR as "collected": data from days ago is reused unless `--refresh` is passed.
