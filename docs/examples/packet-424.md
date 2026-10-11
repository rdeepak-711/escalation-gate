# Issue #424: GitHub enrichment: project_type and contributor counts never reach the evaluator

**Repo:** `interviewstreet/hiring-agent` at `e0ece80` · **Investigation:** 6 (verified, 2026-10-11) · **Investigation cost:** $0.11 · **Prepared by:** escalation-gate

## Summary

The classification is computed in github.py but is lost twice before it reaches the evaluator. First, the selection LLM receives project_type and contributor_count as input, but the prompt's required JSON output schema omits them. The code then uses the model's returned objects directly and never re-attaches the computed fields. Second, transform.convert_github_data_to_text never prints project_type, contributor_count or author_commit_count. The rubric rules that depend on project_type therefore cannot fire, and the evaluator has to guess the type from the description. The report's claim about criteria.jinja is confirmed (line 61). The system_message.jinja claim was only partly checked: grep shows project_type on lines 24 and 71, but I did not read those lines.

**Confidence:** high.
**Recommended:** merge #425 ("GitHub enrichment: pass project_type and contributor counts through to the evaluator"). It was judged to fix the cause from its diff, not run, and it merges clean.
Open PRs judged: 25 (2 fix, 3 partly, 20 don't).

## How it breaks

The path is the model's reasoning. Each step is a citation checked against the code.

1. project_type computed in github.py (`github.py:246` ✅)
2. Fields passed to selection LLM as input (`github.py:352` ✅)
3. Output schema omits project_type/contributor_count (`prompts/templates/github_project_selection.jinja:81` ✅)
4. LLM objects used without re-attaching fields (`github.py:411` ✅)
5. Evaluator text omits type and counts (`transform.py:897` ✅)
6. Rubric rule on project_type cannot fire (`roles/software_engineering_intern/criteria.jinja:61` ✅)

## How to reproduce

1. Run the pipeline with Ollama qwen3:8b on a GitHub profile whose top repo has more than one human contributor and at least 4 commits by the candidate.
2. In github.py, print selected_projects right after json.loads (around line 403). The dicts only have the keys from the template's output schema, with no project_type or contributor_count.
3. Call transform.convert_github_data_to_text on the result, or print the evaluator prompt. Each project shows only name, description, URL, stars, forks and language.
4. The evaluator has no type information, so the rule at criteria.jinja line 61 (all projects 'self_project') gets applied from a guess.
5. To confirm a fix, re-attach the computed fields by repo name after selection and print a Type line in convert_github_data_to_text.

## Root cause

Every citation below was checked against the code at `e0ece80`: the file exists, the line exists, and the quote is on that line (±5 lines). The reasoning that links them is the model's and is **not** checked.

| | Location | Quote | Why it matters |
|---|---|---|---|
| ✅ | `github.py:246` | `project_type = (` | Project type is computed per repo (open_source if contributor_count > 1, else self_project). The counts are stored at lines 259-260. |
| ✅ | `github.py:352` | `"project_type": project.get("project_type", "self_project"),` | The fields are sent to the selection LLM as input, so they exist before selection. |
| ✅ | `prompts/templates/github_project_selection.jinja:81` | `"author_commit_count": 0,` | The output schema (lines 73-98) has author_commit_count and total_commit_count but no project_type or contributor_count, so the model is told to return objects without them. |
| ✅ | `github.py:411` | `unique_projects.append(project)` | The LLM-returned project objects are used as-is. Nothing re-attaches project_type or contributor_count from the original data. Only the fallback path at line 424 keeps them. |
| ✅ | `transform.py:897` | `github_text += f"   URL: {project.get('github_url', 'N/A')}\n"` | The evaluator text for each project (lines 895-902) prints name, description, URL, stars, forks and language. It never prints project_type, contributor_count or author_commit_count. |
| ✅ | `roles/software_engineering_intern/criteria.jinja:61` | `When GitHub data shows all projects are 'self_project' type, open source score MUST be 10 points or less` | The rubric depends on a project_type value that the evaluator text never contains. |

## Open PRs checked against this cause

Each verdict is judged from the diff, not run. Merge status is a real `git merge-tree` check against `e0ece80`.

#415, #425, #242, #414 and #462 all change the cause. Once one is merged, the others duplicate it.

| PR | Verdict | Merge | Why | Shortlisted because |
|---|---|---|---|---|
| #415 feat: add AWS Bedrock provider transport | **fixes** | conflicts | `resolve_selected_projects` rebuilds each selected project from the fetched data (keeping project_type and contributor_count), and transform.py now prints Project Type, Contributors and Author Commits. | changes github.py:411, changes transform.py:897 |
| #425 GitHub enrichment: pass project_type and contributor counts through to the evaluator | **fixes** | merges clean | `restore_repo_metadata()` re-attaches project_type/contributor_count/commit counts right after the selection JSON is parsed, and `convert_github_data_to_text` now prints a Type line with contributors and author commits, so both losses are closed. | mentions the issue, changes transform.py:897 |
| #242 fix: validate LLM bonus claims and reject hallucinated GitHub projects | **partly** | merges clean | It swaps the LLM's project copies for authoritative `valid_by_name` records (restoring the fields), but transform.py is unchanged so the evaluator text still never prints them. | changes github.py:411 |
| #414 Add function to fetch PR contributed repositories | **partly** | merges clean | It pins up to three open_source projects taken from `projects_data` (fields intact), but the remaining projects are still the LLM's objects and transform.py still doesn't print the fields. | changes github.py:411 |
| #462 feat: add ensemble scoring to mitigate LLM non-determinism | **partly** | merges clean | It adds Project Type, Contributors and Author Commits to the evaluator text, but github.py is untouched, so LLM-selected projects still lack those fields and would print as N/A. | changes transform.py:897 |
| #11 fix: GitHub API reliability: encoding issues, retry logic, rate limit handling, and undefined variables #9 | **no** | not checked | It adds retry and rate-limit handling to the GitHub API fetch and leaves selection and evaluator text unchanged. | changes github.py:246 |
| #13 Fix critical GitHub API issues: undefined variables, #9 | **no** | not checked | It only adds API retry, logging and error-handling fixes in github.py; the selection output and transform.py are unchanged. | changes github.py:246, changes github.py:352, changes github.py:411 |
| #34 fix: robust cache file handling for encoding errors | **no** | not checked | It only changes cache-file encoding and error handling. | changes github.py:246 |
| #50 feat: PR based open source contribution detection | **no** | not checked | It changes how project_type is computed and extends the output schema with PR fields, but the schema still omits project_type/contributor_count and transform.py prints neither. | changes github.py:246, changes github.py:352, changes github.py:411, changes prompts/templates/github_project_selection.jinja:81 |
| #54 fix: Resolve dead code and improve logging in github.py (Fixes #46, #37) | **no** | not checked | It tidies logging and the `fetch_repo_contributors` return path and does not touch selection output or evaluator text. | changes github.py:246 |
| #60 Enhance Output Formatting and Readability | **no** | not checked | It changes console output wording and an undefined variable and does nothing about field loss or evaluator text. | changes github.py:246, changes github.py:411 |
| #69 Fix: Add robust error handling for JSON parsing in extract_json_from_response | **no** | not checked | It adds a None guard for malformed JSON and does not re-attach any fields or change evaluator text. | changes github.py:246 |
| #83 Feat: New pipeline to recognize merged open source contributions to organisations | **no** | not checked | It adds a separate open-source-contributions pipeline and leaves the selection output and the per-project text without project_type or contributor counts. | changes github.py:246, changes github.py:411 |
| #148 fix: resolve NameError for undefined 'response' variable in github.py | **no** | not checked | It fixes the undefined `response` NameError and the contributors return type, not the field loss. | changes github.py:246 |
| #165 Correctly fetch and display parent repo stats for forks | **no** | not checked | It changes fork stats to come from the parent repo and does not address field loss after selection or the evaluator text. | changes github.py:246 |
| #176 Improve robustness, scoring, and DX: CLI flags, Live Demo recognition, rate-limit handling, cache validation, docs/ignoresfeat(cli): add --force/--no-github/--max-workers; feat(projects): cap… | **no** | not checked | It adds Repo URL and Live Demo lines to the text and upstream fork details, but still prints no project_type, contributor_count or author commits. | changes github.py:246 |
| #178 Integrate Codeforces profile scoring and bonus points | **no** | not checked | It adds Codeforces/LeetCode scoring and fork handling, and the project text block still omits the classification fields. | changes github.py:246 |
| #329 fix: resolve LLM hallucination of bonus points and GitHub repos (#240) | **no** | not checked | Its whitelist only filters by name and still returns the LLM's echoed objects without project_type, and transform.py is untouched. | changes github.py:411 |
| #376 fix: detect merged PRs to externally-owned repos for open source scoring | **no** | not checked | It adds an external merged-PR section to the text and relaxes the rubric rule, but leaves project_type unprinted and still lost after selection. | changes roles/software_engineering_intern/criteria.jinja:61 |
| #382 fix: correct project details handling for forked repository | **no** | not checked | It changes fork metadata handling only; selection output and the project text lines are unchanged. | changes github.py:246 |
| #394 docs(prompts): expand all template instructions with detailed extraction rules and validation | **no** | not checked | It rewrites prompt text, and nothing visible re-attaches fields in code or prints them in transform.py. | changes prompts/templates/github_project_selection.jinja:81 |
| #405 fix: extracted Github project live demo URLs not being passed to LLM | **no** | not checked | It adds only the live demo URL line to the text and not project_type, contributor_count or author_commit_count. | changes transform.py:897 |
| #410 feat: extract and score Open Source Contributions resume sections | **no** | not checked | It adds a resume-side open source contributions section and rubric text, and neither the lost fields nor the project text is changed. | changes roles/software_engineering_intern/criteria.jinja:61 |
| #444 fix: use filtered project list in every GitHub selection fallback | **no** | not checked | It only makes the fallback paths return the filtered `projects_data`, which already kept the fields, and leaves the success path and the evaluator text unchanged. | changes github.py:352 |
| #453 fix: neutralize forged section delimiters in GitHub/blog data (#452) | **no** | not checked | It sanitizes `===` delimiters in candidate-controlled fields and adds no classification fields to the text or selection output. | changes transform.py:897 |

## Limits of this packet

- Citations are checked against the code. The explanation that links them, and any claim about outside systems (APIs, libraries), is the model's reasoning and is not checked.
- PR verdicts are judged from the diff, not run. No PR was applied or tested.
- PRs were shortlisted because they change a cited line or mention the issue. A fix in another file, or a PR whose line numbers have drifted, is missed, so this is not proof that no other PR fixes it.