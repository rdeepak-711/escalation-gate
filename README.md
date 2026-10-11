# escalation-gate

Give it a GitHub repo and one issue (or a symptom in plain words). It produces an **escalation packet**: the root cause with every code citation checked, steps to reproduce, and which open pull requests already fix it, each with a real merge check.

**Live demo:** [escalation-gate-lemon.vercel.app](https://escalation-gate-lemon.vercel.app) (with a real packet for hiring-agent #424).

It is built for the moment a bug report lands on a busy repo: an engineer wants to know *is this real, where is it, and has someone already fixed it?* without reading 200 open PRs.

## What a packet looks like

On `interviewstreet/hiring-agent` issue #424 (scores swing between runs), one command produced ([see the packet](docs/examples/packet-424.html)):

- **Root cause:** `github.py` labels each repo `project_type`, the project-selection step drops the label, and `transform.py:897` never prints it, so the grader is told to score by a label it never sees. 7 citations, all checked against the code.
- **Recommended:** merge #425, which was judged to fix both halves from its diff and merges clean.
- **Also fixes it:** #415, but it conflicts and the fix is buried inside an unrelated feature PR. **Partly fixes it:** #242 and #462.
- 192 open PRs were cut to 27 by cited lines, then judged, for about $0.40 in total on Sonnet.

I had reviewed the same issue by hand before building the tool. The tool disagreed with my review on #415, and when I checked the diff, the tool was right. It also found #242, which I had missed.

## How it works

```
collect      gh → Postgres: open issues, open PRs, every PR's diff
investigate  Claude Code, read-only (Read/Grep/Glob), finds the cause and cites file:line:quote
check        every citation is verified against the code; on failure, one retry in the same session
shortlist    keep PRs whose diff changes a cited line, or that mention the issue (SQL + diff hunk ranges)
judge        one Claude call, no tools, diffs in the prompt: fixes / partly / no, with one reason each
packet       citations re-checked, git merge-tree per candidate PR, Markdown + HTML written
```

## What is proven and what is opinion

| Part of the packet | Status |
|---|---|
| Citations (file, line, quote) | **Checked** against the code, mechanically |
| "Merges clean" / "conflicts" | **Checked** with `git merge-tree` (a dry-run merge; nothing on disk changes) |
| The explanation that links the citations | Model reasoning, **not** checked |
| Claims about outside systems (e.g. GitHub API behaviour) | Model reasoning, **not** checked |
| PR verdicts (fixes / partly / no) | Judged from the diff, **not run** |
| "No other PR fixes this" | **Not claimed.** A fix in an uncited file, or a PR whose line numbers drifted, is not shortlisted. |

## Run it

Needs: Python 3.11 with [uv](https://docs.astral.sh/uv/), Postgres running locally, the [GitHub CLI](https://cli.github.com/) logged in, and [Claude Code](https://claude.com/claude-code) logged in.

```bash
createdb escalation_gate
uv sync
uv run python -m gate.cli --repo owner/name --issue 123
uv run python -m gate.cli --repo owner/name --symptom "what the user saw, in plain words"
open out/packet-<id>.html
```

To see every open issue with its status (⬜ not investigated, ✅ verified, ⚠️ needs a human), run `uv run python -m gate.cli --repo owner/name --list`. It prints a table and writes `site/<name>/index.html`, a board that links each investigated issue to its packet. Every `--issue` run updates the board.

The first run on a repo clones it into `vendor/` and collects its open issues and PRs (a few minutes for a busy repo). Add `--refresh` to collect again. `--model opus` uses a stronger model for both Claude steps (about 5× the cost).

Each stage can also be run alone: `gate.collect`, `gate.investigate`, `gate.candidates`, `gate.packet`.

Tests (no network, no Claude, no Postgres needed): `uv run pytest -q`

## Limits

- One retry. If the citations still fail, the run is marked `needs_human` and the packet shows which citation failed.
- Tested end to end on two unrelated repos: interviewstreet/hiring-agent and interviewstreet/spark-stratifier.
- The PR verdicts were compared with a careful hand review on two cases only. They agreed, after the hand review was corrected. That is a sanity check, not proof that it generalises.
- Runs vary: the same model on the same issue can go deeper one time than another. The checker catches false citations, not shallow reasoning.

## Next (V2)

Apply each "fixes" PR to a throwaway copy, run the reproduction before and after, and put the measured result in the packet. That turns "judged from the diff" into "measured".

Example output: [`docs/examples/packet-424.html`](docs/examples/packet-424.html) (open it in a browser) and the same packet as [Markdown](docs/examples/packet-424.md). Execution flow by function: [`docs/flow.md`](docs/flow.md).
