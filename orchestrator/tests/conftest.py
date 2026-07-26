import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.models import Base

@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    # autoflush defaults to True here — some tests (e.g. test_email_allowlist.py)
    # rely on this to see pending writes via a later db.query(...) without an
    # explicit db.flush(); changing this default would silently break them.
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class SessionCM:
    """Wrap a SQLAlchemy session (or a factory producing one) as a context
    manager, for patching SessionLocal in code under test. Only closes the
    session on exit if it was created here from a factory — the more
    correct of the ~4 slightly different local copies of this same class
    that existed across the test suite before consolidation; some never
    closed the session.

    Two real usage shapes appear in this suite:
      - Shared/never-closed session (the common case — most call sites):
        patch("app.module.SessionLocal", return_value=SessionCM(db))
        Here `db` is the test's own already-open session fixture, not
        callable, so `_owns_session` stays False and SessionCM never closes
        it — the test fixture's own teardown closes it instead.
      - Fresh factory-owned session, closed per call (used when the code
        under test must see data committed through a *separate* session,
        e.g. test_admin_api.py):
        patch("app.module.SessionLocal", side_effect=lambda: SessionCM(Session))
        or monkeypatch.setattr("app.module.SessionLocal", lambda: SessionCM(Session))
        Here `Session` is a sessionmaker (callable), so each call creates
        and later closes its own session.
    """
    def __init__(self, session_or_factory):
        self._session_or_factory = session_or_factory
        self._session = None
        self._owns_session = False

    def __enter__(self):
        if callable(self._session_or_factory):
            self._session = self._session_or_factory()
            self._owns_session = True
        else:
            self._session = self._session_or_factory
        return self._session

    def __exit__(self, *exc):
        if self._owns_session and self._session is not None:
            self._session.close()
