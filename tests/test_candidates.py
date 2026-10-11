"""Step 05 tests: shortlist open PRs from the investigation's citations, then check Claude's verdicts."""
import gate.candidates as cand

PATCH = """@@ -240,10 +240,14 @@ def fetch_all_github_repos(url):
     repos = []
+    owner = repo["owner"]["login"]
@@ -890,5 +894,6 @@ def convert_github_data_to_text(data):
+    github_text += f"   Type: {project.get('project_type')}\\n"
"""


def test_hunk_ranges_reads_the_old_side():
    assert cand.hunk_ranges(PATCH) == [(240, 249), (890, 894)]


def test_hunk_without_a_length_is_one_line():
    assert cand.hunk_ranges("@@ -7 +7 @@\n-a\n+b\n") == [(7, 7)]


def test_empty_patch_has_no_hunks():
    assert cand.hunk_ranges("") == []


def test_cited_lines_inside_a_hunk_are_hits():
    assert cand.lines_hit(PATCH, [246, 300, 892]) == [246, 892]


def test_rank_puts_issue_mentions_first_then_most_hits():
    rows = [
        (50, "github.py", PATCH),        # 2 cited lines hit
        (60, "github.py", "@@ -1,3 +1,3 @@\n"),   # touches the file, no cited line
        (70, "transform.py", PATCH),     # 1 cited line hit
    ]
    cited = {"github.py": [246, 892], "transform.py": [246]}
    ranked = cand.rank(rows, mentioned={70}, cited=cited)
    assert [c["pr"] for c in ranked] == [70, 50]
    assert ranked[0]["reasons"] == ["mentions the issue", "changes transform.py:246"]
    assert ranked[1]["reasons"] == ["changes github.py:246", "changes github.py:892"]


def test_a_pr_that_only_mentions_the_issue_is_kept():
    ranked = cand.rank([], mentioned={99}, cited={"github.py": [1]})
    assert ranked == [{"pr": 99, "reasons": ["mentions the issue"]}]


def test_cited_files_groups_lines_per_file():
    report = {"evidence": [
        {"file": "github.py", "line": 239}, {"file": "transform.py", "line": 897},
        {"file": "github.py", "line": 204},
    ]}
    assert cand.cited_files(report) == {"github.py": [239, 204], "transform.py": [897]}


def test_verdicts_must_cover_the_shortlist_exactly():
    verdicts = [
        {"pr": 425, "verdict": "fixes", "why": "w"},
        {"pr": 425, "verdict": "no", "why": "w"},
        {"pr": 999, "verdict": "no", "why": "w"},
    ]
    assert cand.check_verdicts(verdicts, [425, 462]) == [
        "PR #425 judged more than once",
        "PR #999 was not on the shortlist",
        "PR #462 has no verdict",
    ]


def test_complete_verdicts_pass():
    verdicts = [{"pr": 425, "verdict": "fixes", "why": "w"}, {"pr": 462, "verdict": "partly", "why": "w"}]
    assert cand.check_verdicts(verdicts, [425, 462]) == []


def test_long_patches_are_cut_and_say_so():
    text = cand.clip("x" * 50, limit=20)
    assert text.startswith("x" * 20) and "30 more characters cut" in text
