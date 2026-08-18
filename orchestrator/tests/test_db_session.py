"""Tests for app.db.session's SQLite connection configuration.

Uses a fresh in-memory engine with the same pragma listener the production
engine registers, rather than app.db.session.engine directly — that engine
is bound to settings.database_url at import time, which points at a real
file path that only exists on the deployed host, not in a local dev/CI
environment.
"""

from sqlalchemy import create_engine, event, text

from app.db.session import set_sqlite_pragma


def test_sqlite_busy_timeout_is_set_generously():
    """Python's sqlite3 module already defaults to a 5000ms busy_timeout —
    not 0 — but that's not enough here: request_confirmation() holds its
    write transaction open across an awaited HTTP call to the bridge
    (flush → await bridge_client.send_message → commit), so when the agent
    fires several tool calls in one turn via asyncio.gather, concurrent
    writers can queue behind each other for longer than 5s combined and
    still hit 'database is locked' — the confirmed root cause of a real
    production bug where a multi-person expense split silently recorded
    only one person's share, no error shown to anyone. We explicitly set a
    longer timeout so that queueing (not immediate failure) is what happens.
    """
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    event.listen(test_engine, "connect", set_sqlite_pragma)

    with test_engine.connect() as conn:
        result = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert result is not None and result >= 15000, (
        f"busy_timeout={result} — Python's sqlite3 default (5000ms) is not "
        f"enough headroom for a write held open across a bridge HTTP call"
    )
