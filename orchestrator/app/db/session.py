from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
from app.db.models import Base

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # Required for SQLite + FastAPI
)

# Enable WAL mode for better concurrent read performance
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # WAL still serializes writers. Python's sqlite3 module already retries
    # for 5s (its own default) before raising "database is locked", but
    # that's not enough headroom: request_confirmation() holds its write
    # transaction open across an awaited HTTP call to the bridge, and the
    # agent runs several tool calls concurrently via asyncio.gather (e.g.
    # one per person in a multi-way expense split) — so a queued writer can
    # need to wait out several such HTTP round-trips in front of it. Without
    # enough headroom here, a multi-person split can silently record only
    # some people's shares with no error surfaced to anyone.
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db():
    """Create all tables. Alembic handles migrations in production."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """FastAPI dependency for DB sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
