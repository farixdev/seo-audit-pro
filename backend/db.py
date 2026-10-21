# -*- coding: utf-8 -*-
"""
Database access layer.

PostgreSQL (Neon) in production via a thread-safe connection pool; SQLite for
local development and the automated test-suite. SQL is written once with
``%s`` placeholders and adapted for SQLite.

Usage::

    rows = db.query("SELECT * FROM users WHERE team_id = %s", (team_id,))
    row = db.query_one("SELECT ...", params)
    with db.transaction() as tx:
        tx.execute("UPDATE ...", params)
        n = tx.rowcount
"""

import re
import sqlite3
import threading
from contextlib import contextmanager

from backend import config

_POOL = None
_POOL_LOCK = threading.Lock()
DIALECT = "postgres" if config.DATABASE_URL else "sqlite"


class IntegrityError(Exception):
    """Raised for unique / foreign-key / check-constraint violations."""


def _clean_pg_url(url):
    # libpq builds bundled with older psycopg2 wheels reject channel_binding.
    return re.sub(r"([?&])channel_binding=[^&]*&?", r"\1", url).rstrip("?&")


def _pg_pool():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                from psycopg2.pool import ThreadedConnectionPool
                _POOL = ThreadedConnectionPool(1, 12, _clean_pg_url(config.DATABASE_URL), connect_timeout=10)
    return _POOL


def _sqlite_connect():
    conn = sqlite3.connect(config.SQLITE_PATH, timeout=30, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def adapt(sql):
    if DIALECT == "sqlite":
        return sql.replace("%s", "?")
    return sql


def _wrap_integrity(exc):
    name = type(exc).__name__
    if isinstance(exc, sqlite3.IntegrityError) or name in ("IntegrityError", "UniqueViolation", "ForeignKeyViolation", "CheckViolation"):
        return IntegrityError(str(exc).split("\n")[0])
    return None


class Tx:
    """Thin cursor wrapper used inside ``transaction()``."""

    def __init__(self, conn):
        self.conn = conn
        if DIALECT == "postgres":
            from psycopg2.extras import RealDictCursor
            self.cur = conn.cursor(cursor_factory=RealDictCursor)
        else:
            self.cur = conn.cursor()
        self.rowcount = 0

    def execute(self, sql, params=()):
        try:
            self.cur.execute(adapt(sql), tuple(params))
        except Exception as e:  # noqa: BLE001 - normalised below
            wrapped = _wrap_integrity(e)
            if wrapped:
                raise wrapped from e
            raise
        self.rowcount = self.cur.rowcount
        return self

    def executemany(self, sql, seq):
        try:
            self.cur.executemany(adapt(sql), [tuple(p) for p in seq])
        except Exception as e:  # noqa: BLE001
            wrapped = _wrap_integrity(e)
            if wrapped:
                raise wrapped from e
            raise
        self.rowcount = self.cur.rowcount
        return self

    def bulk_insert(self, table, columns, rows, conflict_column=None):
        """Insert many rows efficiently. With ``conflict_column`` rows that
        would violate its unique constraint are skipped.

        Returns the set of values of the first column for rows actually
        inserted (callers pass the primary key first)."""
        if not rows:
            return set()
        cols = ", ".join(columns)
        conflict = f" ON CONFLICT ({conflict_column}) DO NOTHING" if conflict_column else ""
        if DIALECT == "postgres":
            from psycopg2.extras import execute_values
            try:
                inserted = execute_values(self.cur, f"INSERT INTO {table} ({cols}) VALUES %s{conflict} RETURNING {columns[0]}",
                                          [tuple(r) for r in rows], page_size=1000, fetch=True)
            except Exception as e:  # noqa: BLE001
                wrapped = _wrap_integrity(e)
                if wrapped:
                    raise wrapped from e
                raise
            return {list(r.values())[0] if isinstance(r, dict) else r[0] for r in inserted}
        marks = ", ".join(["?"] * len(columns))
        verb = "INSERT OR IGNORE" if conflict_column else "INSERT"
        before = self.conn.total_changes
        try:
            self.cur.executemany(f"{verb} INTO {table} ({cols}) VALUES ({marks})", [tuple(r) for r in rows])
        except Exception as e:  # noqa: BLE001
            wrapped = _wrap_integrity(e)
            if wrapped:
                raise wrapped from e
            raise
        if self.conn.total_changes - before == len(rows):
            return {r[0] for r in rows}
        placeholders = ",".join(["?"] * len(rows))
        found = self.conn.execute(f"SELECT {columns[0]} FROM {table} WHERE {columns[0]} IN ({placeholders})",
                                  [r[0] for r in rows]).fetchall()
        return {row[0] for row in found}

    def fetchall(self):
        return [dict(r) for r in self.cur.fetchall()]

    def fetchone(self):
        r = self.cur.fetchone()
        return dict(r) if r is not None else None

    def query(self, sql, params=()):
        return self.execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        return self.execute(sql, params).fetchone()

    def scalar(self, sql, params=()):
        row = self.execute(sql, params).fetchone()
        if not row:
            return None
        return next(iter(row.values()))


@contextmanager
def transaction(readonly=False):
    """Runs the block in a single transaction; commits on success, rolls back
    on any exception. ``readonly`` avoids taking SQLite's write lock."""
    if DIALECT == "postgres":
        pool = _pg_pool()
        conn = pool.getconn()
        broken = False
        try:
            conn.autocommit = False
            tx = Tx(conn)
            yield tx
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                broken = True
            raise
        finally:
            pool.putconn(conn, close=broken or conn.closed != 0)
    else:
        conn = _sqlite_connect()
        try:
            conn.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
            tx = Tx(conn)
            yield tx
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            conn.close()


def query(sql, params=()):
    with transaction(readonly=True) as tx:
        return tx.query(sql, params)


def query_one(sql, params=()):
    with transaction(readonly=True) as tx:
        return tx.query_one(sql, params)


def scalar(sql, params=()):
    with transaction(readonly=True) as tx:
        return tx.scalar(sql, params)


def execute(sql, params=()):
    with transaction() as tx:
        tx.execute(sql, params)
        return tx.rowcount


def like_escape(term):
    """Escape LIKE wildcards in user-provided search terms."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


LIKE = "LIKE %s ESCAPE '\\'"
