"""A throwaway Postgres database per test, for the tests that check the SQL itself. It needs the local Postgres."""
import json
import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

import gate.db as gdb

ADMIN_URL = os.environ.get("GATE_TEST_ADMIN_URL", "postgresql://localhost/postgres")


@pytest.fixture
def db(monkeypatch):
    """A fresh database with the schema made by gate.db.connect(); every gate module's connect() points at it."""
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.fail(f"these tests need the local Postgres ({ADMIN_URL}): {exc}")
    name = f"gate_test_{uuid.uuid4().hex[:12]}"
    admin.execute(f'CREATE DATABASE "{name}"')
    url = make_conninfo(ADMIN_URL, dbname=name)
    monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=5000")  # a lock wait fails the test in 5 s instead of hanging
    monkeypatch.setattr(gdb, "DATABASE_URL", url)
    conn = gdb.connect()
    try:
        yield conn
    finally:
        conn.close()
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


def add_issue(conn, n, title="Bug: it breaks", labels=(), thumbs=0, comments=0, repo="o/r"):
    conn.execute("INSERT INTO issues (repo, number, title, body, labels, thumbs_up, comments) "
                 "VALUES (%s, %s, %s, '', %s, %s, %s)", (repo, n, title, list(labels), thumbs, comments))
    conn.commit()


def add_pr(conn, n, title="", body="", draft=False, repo="o/r"):
    conn.execute("INSERT INTO prs (repo, number, title, body, author, created_at, draft) "
                 "VALUES (%s, %s, %s, %s, 'a', now(), %s)", (repo, n, title, body, draft))
    conn.commit()


def add_inv(conn, issue, status="verified", hidden=False, report=None, repo="o/r"):
    inv_id = conn.execute(
        "INSERT INTO investigations (repo, issue, session_id, status, attempts, report, failures, costs_usd, hidden) "
        "VALUES (%s, %s, %s, %s, 1, %s::jsonb, '{}', '{}', %s) RETURNING id",
        (repo, issue, uuid.uuid4(), status, json.dumps(report) if report else None, hidden)).fetchone()[0]
    conn.commit()
    return inv_id
