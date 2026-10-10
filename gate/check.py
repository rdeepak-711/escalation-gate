"""Step 03: verify every evidence citation (file, line, quote) against the real repo."""
import json
import sys
from pathlib import Path

WINDOW = 5
REQUIRED = ("file", "line", "quote")

def normalize(text: str) -> str:
    """Collapse every run of whitespace to one space, so indentation and line breaks don't matter."""
    return " ".join(text.split())

def check_item(item:dict, repo_dir: Path) -> str | None:
    """Return why this evidence item is wrong, or None if it checks out."""
    missing = [key for key in REQUIRED if key not in item]
    if missing:
        return f"missing {', '.join(missing)}"

    repo = repo_dir.resolve()
    path = (repo / item["file"]).resolve()
    if not path.is_relative_to(repo):
        return f"{item['file']}: outside the repository"
    if not path.is_file():
        return f"{item['file']}: file does not exist"

    lines = path.read_text(errors="replace").splitlines()
    line = item["line"]
    if not isinstance(line, int) or not 1 <= line <= len(lines):
        return f"{item['file']}:{line}: line out of range (file has {len(lines)} lines)"

    quote = normalize(item["quote"])
    if not quote:
        return f"{item['file']}:{line}: quote is empty"
    start = max(0, line - 1 - WINDOW)
    window = normalize("\n".join(lines[start:line + WINDOW]))
    if quote not in window:
        return f"{item['file']}:{line}: quote not found within ±{WINDOW} lines"
    return None

def check(report: dict, repo_dir: Path) -> list[str]:
    """Check every evidence item; return the failures (an empty list means every citation holds)."""
    evidence = report.get("evidence", [])
    if not evidence:
        return ["no evidence items"]
    failures = []
    for n, item in enumerate(evidence, 1):
        problem = check_item(item, repo_dir)
        if problem:
            failures.append(f"evidence {n}: {problem}")
    return failures

if __name__ == "__main__":
    envelope = json.loads(Path(sys.argv[1]).read_text())
    report = envelope.get("structured_output") or envelope
    repo_dir = Path(__file__).resolve().parent.parent / "vendor" /"hiring-agent"
    failures = check(report, repo_dir)
    print("\n".join(failures) or f"all {len(report['evidence'])} citations check out")
    sys.exit(1 if failures else 0)