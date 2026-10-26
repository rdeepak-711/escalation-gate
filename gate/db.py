"""Postgres storage: three tables, every row tagged with its repo"""
import os
import re

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/escalation_gate")

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    repo text NOT NULL,
    number integer NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    labels text[] NOT NULL,
    PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS prs (
    repo text NOT NULL,
    number integer NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    author text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS pr_files (
    repo text NOT NULL,
    pr integer NOT NULL,
    filename text NOT NULL,
    status text NOT NULL,
    additions integer NOT NULL,
    deletions integer NOT NULL,
    patch text NOT NULL,
    PRIMARY KEY (repo, pr, filename),
    FOREIGN KEY (repo, pr) REFERENCES prs(repo, number) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS investigations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo text NOT NULL,
    issue integer,
    symptom text,
    session_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('verified', 'needs_human', 'timeout', 'error')),
    attempts integer NOT NULL,
    report jsonb,
    failures text[] NOT NULL,
    costs_usd double precision[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pr_verdicts (
    investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    pr integer NOT NULL,
    reasons text[] NOT NULL,
    verdict text NOT NULL CHECK (verdict IN ('fixes', 'partly', 'no')),
    why text NOT NULL,
    PRIMARY KEY (investigation_id, pr)
);
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS hidden boolean NOT NULL DEFAULT false;
ALTER TABLE pr_verdicts ADD COLUMN IF NOT EXISTS merge text;
ALTER TABLE prs ADD COLUMN IF NOT EXISTS draft boolean NOT NULL DEFAULT false;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS thumbs_up integer NOT NULL DEFAULT 0;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS comments integer NOT NULL DEFAULT 0;
ALTER TABLE pr_verdicts ADD COLUMN IF NOT EXISTS fix_lines jsonb NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS sweeps (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','stopping','stopped','done','error')),
    pid integer,
    top integer,
    issues integer[],
    model text,
    planned integer[] NOT NULL DEFAULT '{}',
    current integer,
    finished jsonb NOT NULL DEFAULT '[]',
    log text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
ALTER TABLE sweeps ADD COLUMN IF NOT EXISTS max_usd double precision;
ALTER TABLE sweeps ADD COLUMN IF NOT EXISTS cost_usd double precision NOT NULL DEFAULT 0;
ALTER TABLE sweeps ADD COLUMN IF NOT EXISTS stop_reason text;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS judge_cost_usd double precision NOT NULL DEFAULT 0;
ALTER TABLE pr_verdicts ADD COLUMN IF NOT EXISTS side_effects jsonb;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS effects_cost_usd double precision NOT NULL DEFAULT 0;
"""

CONSTRAINTS = {"PRIMARY", "FOREIGN", "CHECK", "UNIQUE"}


def schema_columns(schema: str = SCHEMA) -> set[tuple[str, str]]:
    """Every (table, column) the schema creates or adds, read from the schema text itself"""
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema, re.S)
    added = re.findall(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)", schema)
    if len(tables) != schema.count("CREATE TABLE") or len(added) != schema.count("ALTER TABLE"):
        raise ValueError("every schema statement must be CREATE TABLE IF NOT EXISTS or ADD COLUMN IF NOT EXISTS")
    columns = set(added)
    for table, body in tables:
        for line in body.strip().splitlines():
            name = line.split()[0]
            if name not in CONSTRAINTS:
                columns.add((table, name))
    return columns


COLUMNS = schema_columns()


def connect() -> psycopg.Connection:
    """Connect, and run the schema only when a table or column is missing, so a complete database takes no locks"""
    conn = psycopg.connect(DATABASE_URL)
    present = set(conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()"
    ).fetchall())
    if COLUMNS - present:
        conn.execute(SCHEMA)
    conn.commit()
    return conn
