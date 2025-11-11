import os
from contextlib import contextmanager
from typing import Optional, Dict

import psycopg

DATABASE_URL = os.getenv("DATABASE_URL")


class DatabaseNotConfigured(Exception):
    """Raised when DATABASE_URL is missing but a DB operation was requested."""


@contextmanager
def get_pg_conn():
    if not DATABASE_URL:
        raise DatabaseNotConfigured("DATABASE_URL is not set; Postgres access is unavailable.")
    conn = psycopg.connect(DATABASE_URL)
    try:
        conn.autocommit = False
        yield conn
    finally:
        conn.close()


def ensure_run_requests_table():
    """Creates the run_requests table if it does not exist."""
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS run_requests (
                id SERIAL PRIMARY KEY,
                mode TEXT NOT NULL,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                consumed BOOLEAN NOT NULL DEFAULT FALSE,
                consumed_at TIMESTAMPTZ
            );
            """
        )
        conn.commit()


def enqueue_run_request(mode: str) -> int:
    """Stores a new run request and returns its ID."""
    ensure_run_requests_table()
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO run_requests (mode) VALUES (%s) RETURNING id;",
            (mode,),
        )
        request_id = cur.fetchone()[0]
        conn.commit()
    return request_id


def fetch_next_run_request() -> Optional[Dict]:
    """
    Atomically fetches the oldest unconsumed run request and marks it consumed.
    Returns None when no pending requests exist.
    """
    ensure_run_requests_table()
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH next_request AS (
                SELECT id, mode
                FROM run_requests
                WHERE consumed = FALSE
                ORDER BY requested_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE run_requests rr
            SET consumed = TRUE,
                consumed_at = NOW()
            FROM next_request nr
            WHERE rr.id = nr.id
            RETURNING rr.id, rr.mode, rr.requested_at, rr.consumed_at;
            """
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return {
        "id": row[0],
        "mode": row[1],
        "requested_at": row[2],
        "consumed_at": row[3],
    }
