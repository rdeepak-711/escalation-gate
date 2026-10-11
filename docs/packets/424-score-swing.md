# Escalation packet: same résumé, different scores (hiring-agent #424)

**Repo:** `interviewstreet/hiring-agent` at `e0ece80` (5 Oct 2026) · **Role file:** `software_engineering_intern` · **Prepared:** 2026-10-08 by Deepak R · **Status:** root cause confirmed, fix identified, fix not yet re-measured

## Summary

Scoring the same résumé 15 times gives scores spread over 6 points (3 points at temperature 0). Every category is stable except deductions. The rubric tells the grader to deduct 3–5 points "when GitHub data shows all projects are 'self_project' type", but the GitHub text the grader receives never contains that label, so it guesses. Of the open PRs, two fix this cause completely: #425 (small, merges cleanly) and #415 (inside an unrelated feature, conflicts). Two others fix it only partly, and the rest don't touch it. *(Corrected 2026-10-11: the tool's step 05 verdicts showed #415 fixes both halves and found #242; both were checked by hand against the diffs.)*

## How to reproduce

1. Clone at `e0ece80`. Model `google/gemini-2.5-flash` via OpenRouter, provider pinned. Two local config changes, stated openly: OpenRouter added to `providers.json`, and `structured_output: json_object` (with strict `json_schema`, Gemini via OpenRouter left the bonus fields empty).
2. Score one résumé once so that extraction and GitHub data are cached. After that, only the grading call varies.
3. Score it 15 more times at the default temperature (0.1), then 15 times at temperature 0.

| Setting | Runs | Distribution | Spread |
|---|---|---|---|
| temperature 0.1 (default) | 15 | top score ×10, −2, −3, −5 ×2, −6 | 6 pts |
| temperature 0 | 15 | top score ×7, −3 ×8 | 3 pts |

Per category across all 30 runs: open source, self projects, technical skills and bonus never moved. Production moved twice (−2) and AI fluency once (−1). **Deductions: 0 in 19 runs, 3 in 9, 5 in 2.** The swing comes from the deductions step.

Evidence files: `runs_temp01.txt`, `runs_temp0.txt`, `resume_evaluations_software_engineering_intern.csv`. The CSV holds per-category scores and the model's own reason for each deduction.

## Root cause

| Step | File | What happens |
|---|---|---|
| 1 | `github.py:246–258` | Each repo is labelled `project_type`: `self_project` or `open_source`, based on contributor count. |
| 2 | `github.py` `generate_projects_json` | An LLM picks the top 7 projects. Its output format (`github_project_selection.jinja`, lines 73–97) does **not** include `project_type`. The label survives only if the model happens to copy it across. In this test Gemini did, but nothing guarantees it. |
| 3 | `transform.py:876` `convert_github_data_to_text` | Turns the projects into the text the grader reads: name, description, URL, stars. **`project_type` is never written out.** |
| 4 | `roles/software_engineering_intern/criteria.jinja:61, :176` | "When GitHub data shows all projects are 'self_project' type … open source score MUST be 10 points or less" and "… apply 3-5 point deductions". `system_message.jinja` refers to the same label (lines 24, 71). |

The grader is told to act on a label it cannot see. It infers the label from repo names and descriptions. Some runs deduct 3, and others say the gap is "already reflected in the open-source score" and deduct 0. Temperature 0 makes the guess more consistent but cannot remove it: the inputs are identical, and the decision is still a guess. By the rubric's own intent, the lower score is the correct one, because all 7 cached repos for this résumé are `self_project`.

In short, this is a clear rule whose input is missing. The rule itself is fine.

## Open PRs checked against this cause

I checked all 191 open PRs. Three write `project_type` into the grader's text. The others that mention consistency, scores or determinism were read for whether they touch the missing input. Merge checks were run with `git merge-tree` against `e0ece80`.

| PR | What it changes | Fixes this cause? | Merges clean |
|---|---|---|---|
| **#425** "pass project_type and contributor counts through to the evaluator" | Copies `project_type` and the commit counts back onto the selected projects by repo name (`restore_repo_metadata`, github.py), **and** prints them in `convert_github_data_to_text`. 2 files, +36 lines. References #424. | **Yes, both halves.** The label can no longer be lost at the selection step, and the grader now sees it. | Yes |
| #462 "ensemble scoring to mitigate LLM non-determinism" | Runs the grader N times and takes the median. Also prints `project_type` in `convert_github_data_to_text`. | **Partly.** It prints the label but doesn't restore it after selection, so it shows `N/A` whenever the selection model drops the field. The median narrows the spread at N× the cost, but it hides the guess rather than removing it. | Yes |
| #415 "add AWS Bedrock provider transport" | A new provider. It also adds `resolve_selected_projects` (github.py), which rebuilds every selected project from the fetched data, and prints `project_type`, contributors and commit counts in `convert_github_data_to_text`. | **Yes, both halves** *(corrected 2026-10-11; first version said "partly")*. But the fix is buried in an unrelated feature PR. | **No, conflicts** |
| #242 "validate LLM bonus claims and reject hallucinated GitHub projects" | Replaces each selected project with the fetched record (`valid_by_name[...]`, github.py). | **Partly** *(added 2026-10-11)*. The label survives selection, but `transform.py` is untouched, so the grader still never sees it. | not checked |
| #376, #410 | Reword the `self_project` rule in `criteria.jinja`. | **No.** The reworded rule still depends on the label the grader can't see. | not checked |
| #301, #292, #419, #471, and the temperature or provider PRs | Remove PII from the grader's input, clamp score ranges, add score explanations after scoring, add an evidence trace, change sampling settings. | **No.** None of them gives the grader the missing label. Sampling changes are covered by the temperature-0 run above: 3 points remain. | not checked |

## Recommended next step

1. Merge #425. It's small (2 files), on target, and merges cleanly.
2. Re-run the same 15-run test on `e0ece80` + #425. **Expected:** deductions settle at one value, and the spread falls to production and AI fluency noise (≤2 points). **Not yet measured.** This line gets updated with the real numbers.
3. Prevent the next one: add a check that every input field a rubric rule depends on is present in the rendered grader text. The same pattern can hide in any rule that says "when the data shows X".

## Related, separate bug (not reported anywhere I could find)

`github.py:239` looks up contributors as `fetch_repo_contributors(username, repo_name)`, using the candidate's username rather than the repo owner. For repos owned by someone else, where the candidate is a collaborator (company, team or hackathon repos), GitHub returns Not Found. The candidate gets 0 commits, and `generate_projects_json` silently drops the repo. One-line fix: `fetch_repo_contributors(repo["owner"]["login"], repo_name)`. None of the 50 open PRs that touch `github.py` fixes this line. #165 fixes the owner only for forks.

## Limits of this packet

- One résumé, one role file, one model, 30 runs. The cause is shown by reading the code and by the deduction reasons in the CSV, not by statistics.
- Two local config changes (above) were needed to run on OpenRouter.
- Nothing was posted to GitHub, and no PR code has been run yet.
