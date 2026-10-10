"""Step 03 tests: check() must pass real citations and catch wrong ones."""
import json
from pathlib import Path

import pytest

from gate.check import check

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "out" / "probe-424.json"
VENDOR = ROOT / "vendor" / "hiring-agent"

@pytest.fixture
def repo(tmp_path):
    """A fake repository with one 30-line file; line 4 and line 20 hold real code."""
    lines = [f"# filler {n}" for n in range(1, 31)]
    lines[3] = "def convert_github_data_to_text(github_data):"
    lines[19] = "    return github_text"
    (tmp_path / "transform.py").write_text("\n".join(lines) + "\n")
    return tmp_path

def item(file = "transform.py", line = 4, quote = "def convert_github_data_to_text(github_data):"):
    return {"file": file, "line": line, "quote": quote, "why": "test"}

def report(*evidence):
    return {"evidence": list(evidence)}

# --- citations that must pass ---

def test_exact_citation_passes(repo):
    assert check(report(item()), repo) == []


def test_line_a_few_off_still_passes(repo):
    assert check(report(item(line=7)), repo) == []


def test_extra_whitespace_in_quote_passes(repo):
    quote = "  def    convert_github_data_to_text(github_data):  "
    assert check(report(item(quote=quote)), repo) == []


def test_quote_can_be_part_of_a_line(repo):
    assert check(report(item(line=20, quote="return github_text")), repo) == []

# --- citations that must fail ---

def test_line_far_away_fails(repo):
    failures = check(report(item(line=15)), repo)
    assert len(failures) == 1 and "quote not found" in failures[0]


def test_made_up_quote_fails(repo):
    failures = check(report(item(quote="def score_resume(resume):")), repo)
    assert len(failures) == 1 and "quote not found" in failures[0]


def test_missing_file_fails(repo):
    failures = check(report(item(file="nope.py")), repo)
    assert "does not exist" in failures[0]


def test_line_out_of_range_fails(repo):
    failures = check(report(item(line=999)), repo)
    assert "out of range" in failures[0]


def test_path_outside_repo_fails(repo):
    failures = check(report(item(file="../secret.txt")), repo)
    assert "outside the repository" in failures[0]


def test_missing_quote_key_fails(repo):
    bad = {"file": "transform.py", "line": 4, "snippet": "def convert_github_data_to_text(github_data):"}
    failures = check(report(bad), repo)
    assert "missing quote" in failures[0]


def test_no_evidence_fails(repo):
    assert check({"evidence": []}, repo) == ["no evidence items"]


def test_only_the_bad_item_is_reported(repo):
    failures = check(report(item(), item(line=999)), repo)
    assert len(failures) == 1 and failures[0].startswith("evidence 2:")

# --- the real thing: step 01's output against the real repo ---

@pytest.mark.skipif(not (PROBE.exists() and VENDOR.exists()), reason="needs out/probe-424.json and vendor/hiring-agent")
def test_real_probe_output_passes():
    report = json.loads(PROBE.read_text())["structured_output"]
    assert check(report, VENDOR) == []