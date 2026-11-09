import os
import random
import time

import psycopg

DSN = os.environ["DATABASE_URL"]


class Deadline(Exception):
    pass


def connect():
    return psycopg.connect(DSN, connect_timeout=2)


def init_schema():
    with connect() as conn:
        conn.autocommit = True
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS boards (
              id integer PRIMARY KEY,
              name text NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO boards (id, name) VALUES (1, 'general')
            ON CONFLICT (id) DO NOTHING
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
              id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              board_id integer NOT NULL,
              body text NOT NULL,
              idempotency_key text UNIQUE,
              trace_id text NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              processed_at timestamptz
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              post_id bigint NOT NULL,
              trace_id text NOT NULL,
              parent_span_id text NOT NULL,
              status text NOT NULL,
              attempts integer NOT NULL DEFAULT 0,
              created_at timestamptz NOT NULL DEFAULT now(),
              available_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status, id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_events (
              id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              received_at timestamptz NOT NULL DEFAULT now(),
              alertname text NOT NULL,
              severity text NOT NULL,
              status text NOT NULL
            )
            """
        )


def run_query(fn, deadline_ms, retries):
    """Run fn(conn). Retry only a broken connection, and only inside the deadline."""
    started = time.monotonic()
    attempt = 0
    delay = 0.05
    while True:
        remaining = deadline_ms / 1000 - (time.monotonic() - started)
        if remaining <= 0:
            raise Deadline()
        try:
            with connect() as conn:
                conn.autocommit = True
                timeout_ms = max(1, int(remaining * 1000))
                conn.execute(f"SET statement_timeout = {timeout_ms}")
                return fn(conn), attempt
        except psycopg.OperationalError:
            attempt += 1
            remaining = deadline_ms / 1000 - (time.monotonic() - started)
            if attempt > retries or remaining <= 0.05:
                raise
            time.sleep(min(delay, remaining / 2) + random.random() * 0.02)
            delay *= 2
