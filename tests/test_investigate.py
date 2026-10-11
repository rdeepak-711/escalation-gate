"""Step 04 tests: the investigate loop (check, retry once, give up) without calling Claude."""
import pytest

import gate.investigate as inv

GOOD = {"file": "transform.py", "line": 4, "quote": "def convert_github_data_to_text(github_data):", "why": "t"}
BAD = {**GOOD, "line": 25}

@pytest.fixture
def repo(tmp_path, monkeypatch):
    lines = [f"# filler {n}" for n in range(1, 31)]
    lines[3] = "def convert_github_data_to_text(github_data):"
    (tmp_path / "transform.py").write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(inv, "RUNS_DIR", tmp_path / "runs")
    return tmp_path

def fake_claude(monkeypatch, *results):
    """Replace run_claude with a stand-in that returns `results` in order and records every call."""
    calls = []

    def fake(prompt, repo_dir, session_id, resume, log, model=None):
        calls.append({"prompt": prompt, "session_id": session_id, "resume": resume})
        return results[len(calls) - 1]

    monkeypatch.setattr(inv, "run_claude", fake)
    return calls

def result(*evidence, cost=0.5):
    report = {"cause": "c", "repro": ["r"], "evidence": list(evidence), "confidence": "high"}
    return {"type": "result", "subtype": "success", "structured_output": report, "total_cost_usd": cost}


def test_good_first_answer_is_verified(repo, monkeypatch):
    calls = fake_claude(monkeypatch, result(GOOD))
    outcome = inv.investigate("Issue #1: x", repo)
    assert outcome["status"] == "verified" and outcome["attempts"] == 1
    assert len(calls) == 1 and calls[0]["resume"] is False


def test_bad_then_fixed_is_verified_on_retry(repo, monkeypatch):
    calls = fake_claude(monkeypatch, result(BAD), result(GOOD))
    outcome = inv.investigate("Issue #1: x", repo)
    assert outcome["status"] == "verified" and outcome["attempts"] == 2
    assert calls[1]["resume"] is True
    assert calls[1]["session_id"] == calls[0]["session_id"]
    assert "transform.py:25: quote not found" in calls[1]["prompt"]


def test_bad_twice_needs_a_human(repo, monkeypatch):
    fake_claude(monkeypatch, result(BAD), result(BAD))
    outcome = inv.investigate("Issue #1: x", repo)
    assert outcome["status"] == "needs_human" and outcome["attempts"] == 2
    assert outcome["failures"] and outcome["report"] is not None


def test_timeout_stops_without_retry(repo, monkeypatch):
    calls = fake_claude(monkeypatch, None)
    outcome = inv.investigate("Issue #1: x", repo)
    assert outcome["status"] == "timeout" and len(calls) == 1


def test_missing_structured_output_is_an_error(repo, monkeypatch):
    fake_claude(monkeypatch, {"type": "result", "subtype": "error_max_turns", "total_cost_usd": 0.1})
    outcome = inv.investigate("Issue #1: x", repo)
    assert outcome["status"] == "error" and "error_max_turns" in outcome["failures"][0]


def test_costs_are_kept_per_attempt(repo, monkeypatch):
    fake_claude(monkeypatch, result(BAD, cost=0.7), result(GOOD, cost=0.2))
    outcome = inv.investigate("Issue #1: x", repo)
    assert outcome["costs_usd"] == [0.7, 0.2]