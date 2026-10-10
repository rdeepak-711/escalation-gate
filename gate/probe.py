"""Step 01 probe: can `claude -p` investigate one issue read-only and return JSON?"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_DIR = ROOT / "vendor" / "hiring-agent"
ISSUE = ROOT / "out" / "issue-424.json"
OUT = ROOT / "out" / "probe-424.json"
TIMEOUT_S = 360

SCHEMA = {
    "type": "object",
    "required": ["cause", "repro", "evidence", "confidence"],
    "properties": {
        "cause": {"type": "string"},
        "repro": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file", "line", "quote", "why"],
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "quote": {"type": "string"},
                    "why": {"type": "string"}
                },
            },
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
}

PROMPT_TEMPLATE = """Investigate this bug report in the current repository.

Issue #{number}: {title}

{body}

Rules:
1. Read the code to find the root cause. Do not guess.
2. Every evidence item must give the file path relative to the repository root,
   the line number, and that line quoted exactly as it appears in the file.
3. If you cannot find evidence, say so and set confidence to "low".
"""

def main() -> int:
    issue = json.loads(ISSUE.read_text())
    prompt = PROMPT_TEMPLATE.format(
        number=issue["number"],
        title=issue["title"],
        body=issue["body"],
    )

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        "--allowedTools", "READ,Grep,Glob",
        "--disallowedTools", "Bash,Edit,Write,Webfetch,WebSearch"
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print(f"Error: claude -p timed out after {TIMEOUT_S} seconds", file=sys.stderr)
        return 1

    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    envelope = json.loads(proc.stdout)
    OUT.write_text(json.dumps(envelope, indent=2))
    print("Envelope keys:", sorted(envelope))
    print(json.dumps(envelope.get("structured_output") or envelope.get("result"), indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())