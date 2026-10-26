# escalation-gate · the mental model

How the whole thing works, in plain words. The test for this doc: Deepak can explain every section out loud without reading it.

## 1. The problem

A popular open-source repo collects bug reports (issues) and proposed fixes (pull requests, PRs) faster than its maintainers can read them. HackerRank's hiring-agent had 162 open issues and 192 open PRs. When someone reports a bug, a maintainer has to answer three questions: is the bug real, where exactly is it in the code, and has someone already sent a fix? Doing that by hand for issue #424 took about 1.5 hours.

## 2. The one-sentence version

You give it a repo and one issue number. It reads the code, writes down the cause with proof (file, line and the exact code on that line), checks that proof against the real files, finds the open PRs that touch those exact lines, says which of them fix the bug, and checks whether each would merge cleanly. Everything ends up on one web page, the **packet**.

## 3. The six stages, as a team of six people

Think of six people passing a folder down a line. Each does one job and never does the next person's.

### Stage 1 · Collect: the clerk

**What it does:** it copies every open issue, every open PR and every PR's changes (the **diff**: the lines a PR removes and adds) from GitHub into a local Postgres database. It uses the GitHub command-line tool `gh`.

**Why this way:** later stages ask questions like "which PRs change line 246 of github.py?" A database answers that in milliseconds and costs nothing. Asking GitHub every time would be slow and run into its rate limits.

**Where it breaks:** GitHub can be down or rate-limit you, and the data goes stale. It's collected once and reused until you pass `--refresh`.

**Real example:** hiring-agent took about 2 minutes and stored 162 issues, 192 PRs and 659 changed files.

### Stage 2 · Investigate: the detective

**What it does:** Claude Code (Anthropic's coding agent, run headless with `claude -p`) reads the issue text, then explores the repo. It may only **read**: its tools are Read, Grep and Glob, so it cannot edit files, run code or go online. It must answer in a fixed shape (a JSON schema): the cause, the steps to reproduce, a confidence level, a list of **evidence items** (file, line, an exact quote of up to 200 characters, and why it matters), and a **chain** (the cause as ordered steps, each pointing at one evidence item).

**Why this way:** read-only means it can't break anything. The fixed answer shape means the next stages can check it with code instead of trusting prose. Quotes are capped at 200 characters because one real line in hiring-agent was over 2,000 characters long.

**Where it breaks:** the detective can be wrong about the *reasoning* even when every quote is real. On the 239 bug it claimed GitHub's API never returns org repos. That was false, and I proved it against the live API. Runs also vary: the same model on the same issue can search more narrowly one time than another.

### Stage 3 · Check: the fact-checker (the heart of the tool)

**What it does:** for every evidence item it opens the file, goes to the cited line and looks for the quoted text within 5 lines either side, ignoring spacing differences. It also checks that the file is inside the repo, that the line number exists, and that every chain step points at a real evidence item.

**If something fails:** the failures are sent back to the *same* conversation (`--resume <session-id>`, so Claude remembers everything it already read) with the exact problem, for example "evidence 3: github.py:392: quote not found within ±5 lines". It gets **one** retry. If it fails again, the run is marked **needs_human**. A run that takes over 6 minutes is stopped and marked **timeout**. Nothing is ever retried after a timeout.

**Why this way:** checking a quote is cheap and mechanical, so it can be done with certainty. Model output is treated as untrusted until it's checked.

**Where it breaks:** it checks quotes, not reasoning. A real quote attached to a wrong explanation passes. That's why the packet labels the explanation as judgement.

### Stage 4 · Shortlist: the filter (no AI)

**What it does:** a diff has headers like `@@ -240,10 …`, meaning "this change replaces lines 240 to 249". The filter keeps a PR when one of its changes covers a line the investigation cited, or when its title or description mentions the issue number (like "#424"). It keeps at most 30.

**Why this way:** the cited lines are already checked facts from Stage 3, so the filter adds no new guess. It needs no model, so it costs nothing. On #424 it cut 192 PRs to 25.

**Where it breaks:** three ways, all silent:
- a fix made in a file nobody cited is never shortlisted
- an old PR written against older code can point at shifted line numbers and be missed
- past 30 candidates, the rest are cut

So the packet never claims "no other PR fixes this".

### Stage 5 · Judge: the reviewer

**What it does:** one model call, with **no tools at all**, reads the bug's cause plus the diffs of the shortlisted PRs and labels each one **fixes**, **partly** or **no**, with one sentence of why. A check makes sure every shortlisted PR gets exactly one verdict.

**Why this way:** all the material is in the prompt, so the judge can't wander off, and its input is exactly what was logged. One call for all PRs is cheaper than one call per PR.

**Where it breaks:** a verdict is an opinion from reading a diff. Nothing is run. Borderline verdicts can flip between runs (#414 went from "no" to "partly").

### Stage 6 · Merge check and packet: the locksmith and the writer

**What it does:** for each PR that fixes or partly fixes the bug, it downloads the PR and runs `git merge-tree`. That's a dry-run merge done in memory, which answers "would this merge cleanly or conflict?" without touching any files. Then it re-checks every citation and writes the packet as Markdown and HTML.

**Why this way:** "merges clean" is a fact the tool can prove, so it's shown as proven. A "fixes" verdict on a PR that conflicts is much less useful to a maintainer.

**Where it breaks:** git answers "conflicts" both for a real conflict and for a PR it couldn't download. The code checks the PR exists first and says "unknown" instead. A tested bug caught that one.

## 4. The one idea everything rests on: checked vs. judgement

| Checked (proven mechanically) | Judgement (labelled as opinion) |
|---|---|
| every file, line and quote | the explanation that links them |
| merges clean / conflicts | the order of the chain |
| | fixes / partly / no |
| | any claim about outside systems (GitHub's API, libraries) |

The packet always says which is which. Interview line: *"Citations are verified, verdicts are an informed opinion, and the packet never hides the difference."*

## 5. Where things are stored

One Postgres database, `escalation_gate`, with six tables:

| Table | One row per |
|---|---|
| `issues` | open issue |
| `prs` | open PR |
| `pr_files` | file a PR changes, with its diff |
| `investigations` | investigation run (status, cost, the full report, `hidden` for known-bad runs) |
| `pr_verdicts` | PR judged for an investigation, with its merge result |

Every row carries the repo name, so one database can hold many repos. Every Claude conversation is also logged event by event in `out/runs/<session-id>.jsonl`, so even a run killed by the timeout leaves a record.

## 6. The commands

```bash
uv run python -m gate.cli --repo owner/name --list          # the board: every open issue, ⬜ not yet · ✅ verified · ⚠️ needs a human
uv run python -m gate.cli --repo owner/name --issue 476     # one issue, end to end; the board updates
uv run python -m gate.cli --repo owner/name --symptom "…"   # no issue number: describe what the user saw
```

For a batch, `--sweep --top 10` refreshes issues and PRs, ranks open issues, and investigates the selected issues one by one. `--sweep --issues 424,431` lets you name the issues directly. A sweep tracks its progress in the `sweeps` table and rewrites the board as each issue finishes.

Claude Code reaches the same workflow through `gate.mcp_server`, a local stdio MCP server. `start_sweep` launches the CLI in the background, `sweep_status` reports progress, `stop_sweep` asks it to stop between issues, and `get_board` returns the current results and local page paths. When Postgres is down or `gh` or `claude` is missing or not logged in, a tool answers with one sentence saying what to install, start or run, not a traceback: an agent can act on a sentence (run `gh auth login`, start Postgres) but can only quote a stack trace back at the user. A confidence label only describes checks that ran: cause citations, added PR lines, merge status, and whether the PR is a draft; the PR itself is never executed. "Top 3" means three issues that have not been looked at yet: issues already done are skipped and do not count. Under each issue's best PR, the board also lists the other PRs that fix or partly fix it, because once one of them is merged the rest are probably duplicates that someone needs to close. A sweep also carries a money cap (default $2, `max_usd`), checked between issues rather than mid-issue: the loop only knows what an issue cost once Claude has finished with it, so one issue can overrun the cap by its own cost, and the sweep then stops before the next one with `stop_reason: cost cap`. `sweep_status` shows the running total, the cap and the stop reason, and each finished issue carries its own cost and, when it failed, the first line of the error, so the agent can say why an issue failed without opening the log file.

A sweep that crashes cannot block the next one. The MCP server checks whether the process behind a "running" sweep is still alive, and a process that has died reads as dead even if nobody has collected its exit yet, so the old row is marked as an error and a new sweep starts. Opening a database connection takes no locks once the tables are complete: `connect()` first looks up which tables and columns exist and only changes the schema when one is missing, so the MCP server never waits for a sweep that is holding the database open while Claude works on an issue.

The first run on a repo clones it into `vendor/` and collects it. `--refresh` collects again. `--model opus` uses a stronger, about 5× more expensive model.

## 7. Numbers worth remembering

- **#424:** 6/6 citations verified, 192 → 25 PRs shortlisted, **#425 recommended** (fixes it, merges clean), $0.11 to investigate + $0.30 to judge.
- **#476:** 8/8 citations verified for $0.04. 11 PRs judged, and it flagged **#477** as one that would crash on merge (confirmed by reading the diff).
- **#432:** a narrower re-run gave confident but wrong top-line advice. That one is kept off the site.
- **239 bug:** the real bug was confirmed on the live API (25 of 99 repos mislabelled on one profile), filed as **issue #479**, fixed in **PR #480**.
- **Tests:** 62, none of which call the model. They fake Claude and test the logic.
- **Tested on two repos:** hiring-agent and spark-stratifier (a cold run on a repo it had never seen, $0.03).

## 8. The stories to tell

1. **It caught my mistake.** My 1.5-hour hand review said #415 only partly fixes #424. The tool said it fully fixes it. I opened the diff, and the tool was right.
2. **It found a PR that would crash.** #477 removes a function parameter but leaves one caller still passing it, so merging it gives a TypeError.
3. **It was wrong once, and the design caught it.** It claimed GitHub's API never returns org repos. That was a claim about the outside world, which the packet labels as unchecked. I tested it: false. The real bug was one line away, and I fixed it upstream.

## 9. Questions you'll be asked, and short answers

- **"Why not just ask the model to fix the bug?"** Because a maintainer needs to *trust* the answer. Checked citations and a real merge check make the packet trustworthy. A bare model answer isn't.
- **"How do you stop hallucinations?"** I don't trust the model; I verify it. Every quote is checked against the file, wrong ones get one retry, and anything I can't verify is labelled as judgement.
- **"Why one retry, not three?"** A second failure usually means the model is confused, not unlucky. A human is cheaper than a loop.
- **"Why Postgres?"** The shortlist is a SQL question ("PRs whose diffs touch these lines"). It also lets one database serve many repos and a board.
- **"What would V2 be?"** Apply the recommended PR to a throwaway copy, run the reproduction before and after, and put the measured result in the packet. That turns "judged from the diff" into "measured".
- **"What's the biggest weakness?"** The shortlist can silently miss a fix in an uncited file, and verdicts are read, not run.
