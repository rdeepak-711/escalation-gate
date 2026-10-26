"""PR-side fix lines: the judge names the diff lines that fix the cause, and each one is checked against the diff."""
import gate.candidates as cd

PATCH = """@@ -10,4 +10,5 @@ def total(scores):
     base = sum(scores)
-    return base
+    capped = min(base, role.max_final_score)
+    return max(capped, role.min_final_score)
     # end
@@ -40,2 +41,3 @@ def other():
     a = 1
+    b = 2
\\ No newline at end of file"""


def test_added_lines_maps_new_side_numbers():
    assert cd.added_lines(PATCH) == {
        11: "    capped = min(base, role.max_final_score)",
        12: "    return max(capped, role.min_final_score)",
        42: "    b = 2",
    }


def test_added_lines_of_an_empty_patch():
    assert cd.added_lines("") == {}


def fl(file, line, quote):
    return {"file": file, "line": line, "quote": quote}


def test_exact_line_is_checked():
    out = cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 12, "return max(capped, role.min_final_score)")])
    assert out == [{"file": "score.py", "line": 12, "quote": "return max(capped, role.min_final_score)",
                    "checked": True}]


def test_line_number_may_drift_by_the_slack():
    assert cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 14, "min(base, role.max_final_score)")])[0]["checked"]
    assert not cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 30, "min(base, role.max_final_score)")])[0]["checked"]
    assert cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 30, "min(base, role.max_final_score)")],
                              slack=20)[0]["checked"]


def test_wrong_file_is_not_checked():
    assert not cd.check_fix_lines({"score.py": PATCH}, [fl("roles.py", 12, "return max(capped")])[0]["checked"]


def test_quote_on_a_removed_line_is_not_checked():
    assert not cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 11, "return base")])[0]["checked"]


def test_too_short_or_blank_quote_is_not_checked():
    assert not cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 42, "b")])[0]["checked"]
    assert not cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 42, "   ")])[0]["checked"]


def test_quote_is_compared_stripped():
    assert cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 42, "  b = 2  ")])[0]["checked"]


def test_input_entries_are_not_mutated():
    lines = [fl("score.py", 42, "b = 2")]
    cd.check_fix_lines({"score.py": PATCH}, lines)
    assert "checked" not in lines[0]


def test_schema_allows_fix_lines_but_does_not_require_them():
    item = cd.VERDICT_SCHEMA["properties"]["verdicts"]["items"]
    assert item["required"] == ["pr", "verdict", "why"]
    lines = item["properties"]["lines"]
    assert lines["type"] == "array"
    assert set(lines["items"]["required"]) == {"file", "line", "quote"}
    assert lines["items"]["properties"]["line"]["type"] == "integer"


def test_prompt_asks_for_new_side_line_numbers():
    assert "line number in the new version of the file" in cd.PROMPT_TEMPLATE


def test_null_or_missing_fields_are_not_checked_and_do_not_crash():
    out = cd.check_fix_lines({"score.py": PATCH}, [
        {"file": "score.py", "line": 12, "quote": None},
        {"file": "score.py", "quote": "return max(capped, role.min_final_score)"},
        {"line": 12, "quote": "return max(capped, role.min_final_score)"},
        {"file": "score.py", "line": None, "quote": "return max(capped, role.min_final_score)"},
    ])
    assert [o["checked"] for o in out] == [False, False, False, False]


def test_a_quote_copied_with_the_diff_plus_still_checks():
    out = cd.check_fix_lines({"score.py": PATCH}, [
        fl("score.py", 12, "+    return max(capped, role.min_final_score)"),
        fl("score.py", 42, "+b = 2"),
    ])
    assert [o["checked"] for o in out] == [True, True]
    assert out[0]["quote"] == "+    return max(capped, role.min_final_score)"  # stored as the model gave it


def test_a_plus_alone_or_a_removed_line_with_plus_is_not_checked():
    out = cd.check_fix_lines({"score.py": PATCH}, [fl("score.py", 11, "+"), fl("score.py", 11, "+    return base")])
    assert [o["checked"] for o in out] == [False, False]


def test_prompt_asks_for_quotes_without_the_plus():
    assert "without the leading +" in cd.PROMPT_TEMPLATE
