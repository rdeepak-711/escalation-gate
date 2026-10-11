# Issue draft for interviewstreet/hiring-agent

**Title:** Bug: contributor counts use the candidate as repo owner, so collaborator and org repos score as self_project with 0 commits

**Body:**

## What happens

`fetch_all_github_repos` (github.py) lists repos with `GET /users/{username}/repos?type=all`, which also returns repos owned by orgs or other users that the candidate is a member of. But the contributor lookup on line 239 always uses the candidate's username as the owner:

```python
contributors_data = fetch_repo_contributors(username, repo_name)
```

So for any repo the candidate doesn't own, it requests `/repos/{candidate}/{repo_name}/contributors`. That returns:

- **404** in most cases. The repo gets `contributor_count: 0`, `author_commit_count: 0` and `project_type: "self_project"`.
- **The wrong repo's data** when the candidate owns a different repo with the same name. No error is raised.

The rubric then scores by that label (`roles/software_engineering_intern/criteria.jinja:61` caps the open source score when all projects are `self_project`), so real team and open source work counts against the candidate.

## Reproduce

Commit `e0ece80`, Python 3.13, `GITHUB_TOKEN` set:

```python
from github import fetch_all_github_repos
projects = fetch_all_github_repos("https://github.com/kentcdodds", max_repos=100)
p = next(p for p in projects if p["name"] == "react-error-boundary")
print(p["github_url"], p["project_type"], p["contributor_count"], p["author_commit_count"])
```

| | Result |
|---|---|
| Now | `https://github.com/bvaughn/react-error-boundary self_project 0 0` |
| Expected | `open_source`, 30 contributors, 16 commits by the candidate (what `/repos/bvaughn/react-error-boundary/contributors` returns) |

Across that profile's 99 repos, 25 are mislabelled: 64 open source / 35 self projects now, 89 / 10 with the owner fixed.

## Fix

Use the repo's real owner, which the API already returns in each repo object:

```python
repo_owner = (repo.get("owner") or {}).get("login") or username
contributors_data = fetch_repo_contributors(repo_owner, repo_name)
```

`fetch_contributions_count(username, contributors_data)` stays as it is. It should still match the candidate's login inside that list.

I'll open a PR with this change.
