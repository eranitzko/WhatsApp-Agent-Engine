import uuid

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


def seed_blueprint(db, id="test_bp", **overrides):
    """Create (or return existing) Blueprint row. Idempotent by id so callers
    can freely call this before seed_group/seed_household without worrying
    about duplicate-PK errors, and so a caller wanting custom fields (e.g.
    display_name) can call this first with overrides, then rely on
    seed_group's auto-seed becoming a no-op for that id."""
    from app.db.models import Blueprint
    existing = db.query(Blueprint).filter_by(id=id).first()
    if existing:
        return existing
    defaults = dict(display_name="Test Blueprint", system_prompt="p", tools_enabled="[]")
    defaults.update(overrides)
    bp = Blueprint(id=id, **defaults)
    db.add(bp)
    db.commit()
    return bp


def seed_group(db, jid, blueprint_id=None, **overrides):
    """Create (or return existing) GroupRegistry row, auto-seeding its
    Blueprint if missing. This makes the FK-ordering bug class
    (GroupRegistry referencing a never-created Blueprint, silently fine only
    because SQLite doesn't enforce FKs by default) structurally impossible.

    Idempotent by jid (group_jid is the primary key) for the same reason as
    seed_blueprint's own idempotency: a caller wanting custom group fields
    can call this first with overrides, then rely on seed_household's
    internal seed_group(db, group_jid, blueprint_id=...) call becoming a
    no-op for that jid instead of raising IntegrityError on the duplicate PK."""
    from app.db.models import GroupRegistry
    existing = db.query(GroupRegistry).filter_by(group_jid=jid).first()
    if existing:
        return existing
    if blueprint_id is None:
        blueprint_id = seed_blueprint(db).id
    else:
        seed_blueprint(db, id=blueprint_id)  # auto-seed if missing — no-op if
        # already created (e.g. by a prior seed_blueprint(db, id=..., **custom)
        # call from the caller wanting non-default blueprint fields).
    defaults = dict(group_type="personal")
    defaults.update(overrides)
    g = GroupRegistry(group_jid=jid, blueprint_id=blueprint_id, **defaults)
    db.add(g)
    db.commit()
    return g


def seed_household(db, phone, group_jid, blueprint_id="family_accounting"):
    """Create Household + HouseholdMember, auto-seeding the GroupRegistry
    (and its Blueprint) referenced by private_group_jid so FK constraints
    hold end-to-end."""
    from app.db.models import Household, HouseholdMember
    seed_group(db, group_jid, blueprint_id=blueprint_id)
    h = Household(name="Test Family")
    db.add(h)
    db.flush()
    m = HouseholdMember(household_id=h.id, phone=phone, private_group_jid=group_jid)
    db.add(m)
    db.commit()
    return h, m


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


def make_invoice(db, **overrides):
    from datetime import date
    from decimal import Decimal
    from app.db.models import Invoice
    defaults = dict(
        id=f"inv-{uuid.uuid4().hex[:8]}",
        group_id="123@g.us",
        message_id=f"msg-{uuid.uuid4().hex[:8]}",
        image_hash=f"hash-{uuid.uuid4().hex[:8]}",
        invoice_date=date.today(),
        vendor="Test Vendor",
        amount_original=Decimal("100"),
        currency_original="ILS",
        amount_ils=Decimal("100"),
    )
    defaults.update(overrides)
    invoice = Invoice(**defaults)
    db.add(invoice)
    db.commit()
    return invoice
