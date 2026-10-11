# PR draft for interviewstreet/hiring-agent

**Branch:** `fix/contributors-repo-owner`
**Commit subject:** `fix: look up contributors under the repo's real owner (#ISSUE)`
**PR title:** fix: look up contributors under the repo's real owner

**Body:**

Fixes #ISSUE.

`fetch_all_github_repos` passed the candidate's username as the owner to `fetch_repo_contributors`. For collaborator and org repos (which `type=all` returns), that requested a repo that doesn't exist (404, so 0 contributors, 0 commits, `self_project`), or a different repo with the same name owned by the candidate.

This uses `repo["owner"]["login"]` instead, falling back to the username if the field is missing.

**Before / after** on `https://github.com/kentcdodds` (99 repos, real API, `GITHUB_TOKEN` set):

| | Before | After |
|---|---|---|
| `bvaughn/react-error-boundary` | `self_project`, 0 contributors, 0 commits | `open_source`, 30 contributors, 16 commits |
| Profile total | 64 open source / 35 self | 89 open source / 10 self |

2 lines changed in `github.py`. Formatted with Black 25.9.0. No change to `fetch_contributions_count` or the scoring prompts.
