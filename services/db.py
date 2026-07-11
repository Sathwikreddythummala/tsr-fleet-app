"""PostgreSQL connection handling with commit/rollback and a self-healing pool.

Neon (and other serverless Postgres providers) close idle connections after a
few minutes. The pool hands those dead connections back, so every query goes
through a retry path: on a connection-level error the pool is rebuilt and the
statement is retried once on a fresh connection.
"""
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from config import settings

_pool = None

CONNECTION_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)


def get_pool():
    global _pool
    if _pool is None:
        if not settings.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured")
        _pool = SimpleConnectionPool(1, 10, dsn=settings.DATABASE_URL)
    return _pool


def reset_pool():
    """Discard every pooled connection (used after a connection-level failure)."""
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
        except Exception:
            pass
    _pool = None


@contextmanager
def get_conn():
    pool = get_pool()
    conn = pool.getconn()
    broken = False
    try:
        yield conn
        conn.commit()
    except Exception:
        broken = getattr(conn, "closed", False)
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        try:
            pool.putconn(conn, close=broken or bool(getattr(conn, "closed", False)))
        except Exception:
            pass


def execute(query, params=None, fetch=False):
    """Run a SQL statement, retrying once on a stale/dropped connection.

    Returns a list of dict rows when fetch=True.
    """
    try:
        return _run(query, params, fetch)
    except CONNECTION_ERRORS:
        reset_pool()
        return _run(query, params, fetch)


def _run(query, params, fetch):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params or ())
            if fetch:
                return [dict(r) for r in cur.fetchall()]
    return None


def fetch_all(query, params=None):
    return execute(query, params, fetch=True)


def fetch_one(query, params=None):
    rows = execute(query, params, fetch=True)
    return rows[0] if rows else None
