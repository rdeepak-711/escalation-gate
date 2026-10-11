# Execution flow

How a run travels through the code, file by file. Status as of 2026-10-11: steps 01–06 are built and tested (38 tests). Step 07 (one-command CLI + README) is **planned**.

## The whole path (V1)

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
| **Postgres** | `psycopg` via `gate/db.py` `connect()` (which also creates all five tables) | Rows in and out | Server down; a SQL typo in `SCHEMA` crashes every command at `connect()` (seen 2026-10-11) |

## Step by step, with the real functions

1. **Collect** — `gate/collect.py` `collect(repo)`: `gh issue list` → upsert `issues`, delete closed; `gh pr list` → upsert `prs`, delete closed (cascade removes their `pr_files`); per PR `gh api …/pulls/N/files` → replace its `pr_files` rows; commit per PR.
2. **Investigate** — `gate/investigate.py` `main()` reads the issue from Postgres (or takes `--symptom`), calls `investigate()`:
   - `run_claude(prompt, …, resume=False)` with a new `--session-id`.
   - `check(report, repo_dir)` (`gate/check.py`): file inside repo, exists, line in range, quote within ±5 lines (whitespace-normalised).
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
   - `render_markdown()` / `render_html()` fill `gate/templates/packet.md.j2` / `packet.html.j2` (HTML autoescaped); `recommend()` picks the first "fixes" PR that merges clean.

## What the 2026-10-11 session touched

- Steps 04 (Deepak typed it; Claude fixed the trailing comma in `db.py` and the `-"--resume"` bug), 05 and 06 (Claude wrote them after Deepak's "go").
- `run_claude()` gained `schema` and `tools` parameters and now passes `--tools` instead of `--allowedTools`.
- New tables `investigations`, `pr_verdicts`; new modules `gate/candidates.py`, `gate/packet.py`, `gate/templates/`.
- `docs/packets/424-score-swing.md` corrected (#415 fixes, #242 partly).

## Known gaps in this trace

- How `claude -p` decides which files to read happens inside Claude Code; we only see its event log.
- A `RuntimeError` from `run_claude()` (Claude crashed) stops the program before `save()`, so no row records it.
- Step 07's single entry point (`gate.cli`) does not exist yet; today it is four commands.
