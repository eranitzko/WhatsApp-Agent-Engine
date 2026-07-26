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
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class SessionCM:
    """Wrap a SQLAlchemy session (or a factory producing one) as a context
    manager, for patching SessionLocal in code under test. Closes the
    session on exit — the more correct of the ~4 slightly different local
    copies of this same class that existed across the test suite before
    consolidation; some never closed the session.

    Usage: patch("app.module.SessionLocal", side_effect=lambda: SessionCM(db))
    or, if the code under test expects SessionLocal to be a zero-arg
    callable itself: patch("app.module.SessionLocal", new=lambda: SessionCM(db)).
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
