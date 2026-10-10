"""Postgres storage: three tables, every row tagged with its repo"""
import os

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
"""

def connect() -> psycopg.Connection:
    """Connect to the database and create tables if they don't exist"""
    conn = psycopg.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()
    return conn