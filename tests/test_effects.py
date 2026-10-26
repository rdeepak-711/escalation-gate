"""The side-effect pass: after the judge, one Claude call per issue asks what else each fixing PR changes, and every
claim is checked against the diff on the side it names. Pure parts first, then the stored result on a real database."""
import json

import pytest

import gate.effects as ef
import gate.sweep as sw
from conftest import add_inv, add_issue, add_pr

PATCH = """@@ -10,4 +10,5 @@ def total(scores):
     base = sum(scores)
-    return base
+    capped = min(base, role.max_final_score)
+    return max(capped, role.min_final_score)
     # end
@@ -40,3 +41,2 @@ def other():
     a = 1
-    github_rows = fetch_github(user)
-    DEFAULT_MODEL = "gemini-2.5-pro"
+    b = 2"""


def e(file="score.py", line=11, side="removed", quote="return base", kind="removed_name", pr=10):
    return {"pr": pr, "kind": kind, "file": file, "line": line, "side": side, "quote": quote, "why": "w"}


# --- pure ----------------------------------------------------------------------------------------------------

def test_removed_lines_maps_old_side_numbers():
    assert ef.removed_lines(PATCH) == {11: "    return base", 41: "    github_rows = fetch_github(user)",
                                       42: '    DEFAULT_MODEL = "gemini-2.5-pro"'}


def test_removed_lines_of_an_empty_patch():
    assert ef.removed_lines("") == {}


def test_a_removed_line_is_checked_on_the_removed_side_only():
    assert ef.check_effects({"score.py": PATCH}, [e()])[0]["checked"]
    assert not ef.check_effects({"score.py": PATCH}, [e(side="added")])[0]["checked"]


def test_an_added_line_is_checked_on_the_added_side_only():
    assert ef.check_effects({"score.py": PATCH}, [e(line=12, side="added", quote="return max(capped")])[0]["checked"]
    assert not ef.check_effects({"score.py": PATCH}, [e(line=12, side="removed", quote="return max(capped")])[0]["checked"]


def test_slack_prefix_and_short_quotes_follow_the_fix_line_rules():
    assert ef.check_effects({"score.py": PATCH}, [e(line=44, quote="DEFAULT_MODEL")])[0]["checked"]  # 42 ± 3
    assert not ef.check_effects({"score.py": PATCH}, [e(line=60, quote="DEFAULT_MODEL")])[0]["checked"]
    assert ef.check_effects({"score.py": PATCH}, [e(line=41, quote="-    github_rows = fetch_github(user)")])[0]["checked"]
    assert not ef.check_effects({"score.py": PATCH}, [e(line=41, quote="git")])[0]["checked"]


def test_wrong_file_unknown_side_and_null_fields_are_unchecked_not_errors():
    out = ef.check_effects({"score.py": PATCH}, [e(file="other.py"), e(side="both"), e(quote=None), e(line=None),
                                                 {"pr": 10, "kind": "other"}])
    assert [o["checked"] for o in out] == [False] * 5


def test_input_is_not_mutated():
    item = e()
    ef.check_effects({"score.py": PATCH}, [item])
    assert "checked" not in item


def test_schema_and_kinds():
    item = ef.EFFECTS_SCHEMA["properties"]["effects"]["items"]
    assert set(item["required"]) == {"pr", "kind", "file", "line", "side", "quote", "why"}
    assert item["properties"]["kind"]["enum"] == ["removed_name", "deleted_data", "changed_default",
                                                  "new_undefined_name", "other"]
    assert item["properties"]["side"]["enum"] == ["added", "removed"]
    assert item["properties"]["line"]["type"] == "integer"
    assert ef.KIND_LABEL["deleted_data"] == "deleted data"


def test_prompt_asks_the_right_question():
    text = ef.PROMPT_TEMPLATE
    for word in ("removes", "deletes", "renames", "default", "undefined"):
        assert word in text
    assert "old version of the file" in text and "new version of the file" in text
    assert "{prs}" in text


def test_build_prompt_shows_every_file_of_each_fixing_pr(db):
    add_issue(db, 2)
    for pr in (10, 11):
        add_pr(db, pr, body="fixes #2")
    db.execute("INSERT INTO pr_files (repo, pr, filename, status, additions, deletions, patch) VALUES "
               "('o/r', 10, 'score.py', 'modified', 2, 1, %s), ('o/r', 10, 'other.py', 'modified', 1, 0, '+x = 1'), "
               "('o/r', 11, 'z.py', 'modified', 1, 0, %s)", (PATCH, "+" + "y" * 20000))
    db.commit()
    prompt = ef.build_prompt(db, "o/r", [{"pr": 10, "verdict": "fixes"}, {"pr": 11, "verdict": "partly"}])
    assert "=== PR #10" in prompt and "--- score.py" in prompt and "--- other.py" in prompt
    assert "=== PR #11" in prompt and "more characters cut" in prompt  # clipped at ef.PATCH_LIMIT per PR
    assert ef.PATCH_LIMIT == 12_000


# --- run, on a real database with the Claude call faked ------------------------------------------------------

@pytest.fixture
def judged(db):
    add_issue(db, 2)
    for pr in (10, 11, 12):
        add_pr(db, pr, body="fixes #2")
    db.execute("INSERT INTO pr_files (repo, pr, filename, status, additions, deletions, patch) VALUES "
               "('o/r', 10, 'score.py', 'modified', 2, 1, %s), ('o/r', 11, 'score.py', 'modified', 2, 1, %s)",
               (PATCH, PATCH))
    inv = add_inv(db, 2, "verified", report={"cause": "c", "repro": [], "evidence": [], "confidence": "high"})
    db.execute("INSERT INTO pr_verdicts (investigation_id, pr, reasons, verdict, why) VALUES "
               "(%s, 10, '{}', 'fixes', 'w'), (%s, 11, '{}', 'partly', 'w'), (%s, 12, '{}', 'no', 'w')",
               (inv, inv, inv))
    db.commit()
    return db, inv


def stored(db, inv):
    return dict(db.execute("SELECT pr, side_effects FROM pr_verdicts WHERE investigation_id = %s", (inv,)))


def test_run_stores_checked_effects_for_fixing_prs_only(judged, monkeypatch):
    db, inv = judged
    asked = {}

    def ask(prompt, expected, model):
        asked["expected"] = expected
        return [e(pr=10), e(pr=10, line=99, quote="nonsense here"), e(pr=11, line=42, quote="DEFAULT_MODEL", kind="changed_default")], 0.07
    monkeypatch.setattr(ef, "ask", ask)
    assert ef.run(db, inv, "sonnet") == []
    assert asked["expected"] == [10, 11]
    out = stored(db, inv)
    assert [x["checked"] for x in out[10]] == [True, False]
    assert out[11] == [{**e(pr=11, line=42, quote="DEFAULT_MODEL", kind="changed_default"), "checked": True}]
    assert out[12] is None  # a "no" verdict is never asked about
    assert db.execute("SELECT effects_cost_usd FROM investigations WHERE id = %s", (inv,)).fetchone()[0] == 0.07


def test_run_with_no_effects_stores_empty_lists(judged, monkeypatch):
    db, inv = judged
    monkeypatch.setattr(ef, "ask", lambda prompt, expected, model: ([], 0.02))
    ef.run(db, inv, "sonnet")
    assert stored(db, inv) == {10: [], 11: [], 12: None}


def test_a_failed_call_leaves_null_and_reports_it(judged, monkeypatch):
    db, inv = judged
    monkeypatch.setattr(ef, "ask", lambda prompt, expected, model: None)
    assert ef.run(db, inv, "sonnet") == ["side effects not checked"]
    assert stored(db, inv) == {10: None, 11: None, 12: None}


def test_effects_for_a_pr_that_was_not_asked_about_are_dropped(judged, monkeypatch):
    db, inv = judged
    monkeypatch.setattr(ef, "ask", lambda prompt, expected, model: ([e(pr=12), e(pr=99)], 0.01))
    ef.run(db, inv, "sonnet")
    assert stored(db, inv) == {10: [], 11: [], 12: None}


def test_run_without_fixing_prs_asks_nothing(db, monkeypatch):
    add_issue(db, 2)
    add_pr(db, 12, body="#2")
    inv = add_inv(db, 2, "verified", report={"cause": "c", "repro": [], "evidence": [], "confidence": "high"})
    db.execute("INSERT INTO pr_verdicts (investigation_id, pr, reasons, verdict, why) VALUES (%s, 12, '{}', 'no', 'w')",
               (inv,))
    db.commit()
    monkeypatch.setattr(ef, "ask", lambda *a: pytest.fail("must not call Claude"))
    assert ef.run(db, inv, "sonnet") == []


def test_process_issue_runs_the_pass_after_judging_and_counts_its_cost(db, monkeypatch):
    add_issue(db, 2)
    inv = add_inv(db, 2, "verified", report={"cause": "c", "repro": [], "evidence": [], "confidence": "high"})
    db.execute("UPDATE investigations SET costs_usd = '{0.2}', judge_cost_usd = 0.05, effects_cost_usd = 0.03 "
               "WHERE id = %s", (inv,))
    db.commit()
    order = []
    monkeypatch.setattr(sw.investigate, "run", lambda conn, repo, issue, symptom, model: (inv, {"status": "verified"}))
    monkeypatch.setattr(sw.candidates, "run", lambda conn, inv_id, model: order.append("judge") or [])
    monkeypatch.setattr(sw.effects, "run", lambda conn, inv_id, model: order.append("effects") or [])
    monkeypatch.setattr(sw.packet, "write", lambda conn, inv_id: order.append("packet"))
    out = sw.process_issue(db, "o/r", 2, "sonnet")
    assert order == ["judge", "effects", "packet"]
    assert out["cost"] == pytest.approx(0.28)
