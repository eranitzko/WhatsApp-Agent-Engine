"""Tests for admin panel REST API endpoints."""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.admin.api import router as api_router
from app.db.models import GroupRegistry, AdminNumbers
from tests.conftest import SessionCM, seed_blueprint, seed_group


# -- helpers -----------------------------------------------------------------

def _seed(db):
    seed_blueprint(db, id="fa", display_name="Family Accounting")
    seed_group(db, "111@g.us", blueprint_id="fa")
    db.add(AdminNumbers(phone_number="972500000001"))
    db.commit()


def _make_app(db, *, override_auth=True):
    """Build a FastAPI app with auth optionally bypassed."""
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    if override_auth:
        from app.admin.auth import require_auth
        app.dependency_overrides[require_auth] = lambda: None
    return app


# -- /admin/api/people --------------------------------------------------------

def test_add_person_with_raw_participant_id_creates_group_participant(db):
    """The 'Unknown member' quick-add flow (Groups tab -> click an unresolved
    bridge participant -> supply their real phone) must actually resolve that
    display, not just create a UserAccount the Groups tab never looks at.
    list_groups() names a participant by matching GroupParticipant.phone
    against the bridge's raw reported id (a LID for most senders) — so
    add_person needs to write a GroupParticipant row keyed by that exact raw
    id, not just register the person's real phone."""
    _seed(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.post("/admin/api/people", json={
            "phone": "972501234567",
            "display_name": "Bar Itzkovitch",
            "group_jid": "111@g.us",
            "raw_participant_id": "234973567156224",
        })
        assert resp.status_code == 200

    from app.db.models import GroupParticipant, UserProfile
    verify = Session()
    gp = verify.get(GroupParticipant, ("111@g.us", "234973567156224"))
    assert gp is not None
    assert gp.admin_name == "Bar Itzkovitch"
    assert gp.status == "active"
    profile = verify.query(UserProfile).filter_by(phone="972501234567").first()
    assert profile.known_lid == "234973567156224"
    verify.close()


def test_add_person_raw_participant_id_same_as_phone_skips_known_lid(db):
    """If the bridge already reports the person's real phone (no LID
    obfuscation for that sender), raw_participant_id will equal phone —
    don't record a pointless self-referential known_lid mapping."""
    _seed(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.post("/admin/api/people", json={
            "phone": "972501234568",
            "group_jid": "111@g.us",
            "raw_participant_id": "972501234568",
        })
        assert resp.status_code == 200

    from app.db.models import UserProfile
    verify = Session()
    profile = verify.query(UserProfile).filter_by(phone="972501234568").first()
    assert profile is None or profile.known_lid is None
    verify.close()


def test_add_person_without_raw_participant_id_unchanged(db):
    """Regression: the normal People-tab 'Add person' flow (no raw_participant_id)
    must behave exactly as before — no GroupParticipant/known_lid side effects."""
    _seed(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.post("/admin/api/people", json={
            "phone": "972501234569",
            "display_name": "Someone",
            "group_jid": "111@g.us",
        })
        assert resp.status_code == 200

    from app.db.models import GroupParticipant
    verify = Session()
    count = verify.query(GroupParticipant).filter_by(group_jid="111@g.us").count()
    assert count == 0
    verify.close()


def test_add_person_second_owner_for_same_group_downgrades_to_member(db):
    """Regression: adding a second person to the same group_jid (e.g. fixing
    two 'Unknown member' tiles in one shared group) must not create two
    role='owner' UserAccount rows — resolve_group_owner/get_personal_group_jid
    pick between them non-deterministically if it does."""
    _seed(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        client.post("/admin/api/people", json={"phone": "972501111111", "group_jid": "111@g.us"})
        client.post("/admin/api/people", json={"phone": "972501111112", "group_jid": "111@g.us"})

    from app.db.models import UserAccount
    verify = Session()
    accounts = verify.query(UserAccount).filter_by(group_jid="111@g.us").order_by(UserAccount.phone).all()
    roles = {a.phone: a.role for a in accounts}
    assert roles["972501111111"] == "owner"
    assert roles["972501111112"] == "member"
    verify.close()


def test_add_person_known_lid_conflict_returns_clean_error(db):
    """Assigning a known_lid already claimed by a different phone must not
    surface as an unhandled IntegrityError/500."""
    _seed(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        r1 = client.post("/admin/api/people", json={
            "phone": "972502222221", "group_jid": "111@g.us", "raw_participant_id": "555000111",
        })
        assert r1.status_code == 200
        r2 = client.post("/admin/api/people", json={
            "phone": "972502222222", "group_jid": "111@g.us", "raw_participant_id": "555000111",
        })
        assert r2.status_code == 400
        assert "already" in r2.json()["detail"].lower()


# -- /admin/api/groups -------------------------------------------------------

@pytest.mark.asyncio
async def test_list_groups(db):
    _seed(db)
    db.close()  # flush so the route's new session can see the data

    Session = _get_session_factory(db)

    from app.admin import api as admin_api

    async def _mock_bridge_groups():
        return {"111@g.us": {"name": "Test Group", "participants": []}}

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(admin_api, "_fetch_bridge_groups", _mock_bridge_groups):
        app = _make_app(db)
        client = TestClient(app)
        resp = client.get("/admin/api/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["group_jid"] == "111@g.us"
        assert data[0]["group_name"] == "Test Group"
        assert data[0]["blueprint_name"] == "Family Accounting"


@pytest.mark.asyncio
async def test_list_groups_resolves_member_via_known_lid(db):
    """A bridge participant with no GroupParticipant row for THIS exact raw
    id must still resolve to a name if UserProfile.known_lid already links
    that raw id to a real, named person — not just show 'Unknown member'
    (name: None) purely because they've never messaged in this specific
    group under this exact LID."""
    _seed(db)
    db.close()

    from app.db.models import UserProfile
    Session = _get_session_factory(db)
    seed_db = Session()
    seed_db.add(UserProfile(phone="972501234567", display_name="Bar Itzkovitch", known_lid="234973567156224"))
    seed_db.commit()
    seed_db.close()

    from app.admin import api as admin_api

    async def _mock_bridge_groups():
        return {"111@g.us": {"name": "Test Group", "participants": [
            {"jid": "234973567156224@lid", "isAdmin": False},
        ]}}

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(admin_api, "_fetch_bridge_groups", _mock_bridge_groups):
        app = _make_app(db)
        client = TestClient(app)
        resp = client.get("/admin/api/groups")
        assert resp.status_code == 200
        data = resp.json()
        member = data[0]["members"][0]
        assert member["name"] == "Bar Itzkovitch"


@pytest.mark.asyncio
async def test_list_groups_bridge_fallback(db):
    """When bridge is unreachable, group_name falls back to group_jid."""
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    from app.admin import api as admin_api
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    Session = _get_session_factory(db)

    async def _empty_bridge_groups():
        return {}

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(admin_api, "_fetch_bridge_groups", _empty_bridge_groups):
        client = TestClient(app)
        resp = client.get("/admin/api/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["group_name"] == "111@g.us"  # fallback to JID


@pytest.mark.asyncio
async def test_register_group(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.post("/admin/api/groups",
                           json={"group_jid": "222@g.us", "blueprint_id": "fa"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    verify = Session()
    row = verify.query(GroupRegistry).filter_by(group_jid="222@g.us").first()
    assert row is not None
    assert row.blueprint_id == "fa"
    verify.close()


@pytest.mark.asyncio
async def test_register_group_syncs_existing_participant_to_user_account(db):
    """Regression: someone opens a new private WhatsApp group with the bot
    (passively tracked as a GroupParticipant the moment they send any
    message, regardless of registration status), the admin registers the
    group via the Groups tab's "+ Register Group" — the person was expected
    to then show up in People automatically, matching what
    approve_registration already does for the Pending-Registrations path,
    but register_group never created a UserAccount for them at all."""
    from app.db.models import UserAccount, GroupParticipant
    _seed(db)
    db.add(GroupParticipant(group_jid="333@g.us", phone="972500009999", status="active"))
    db.commit()
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.post("/admin/api/groups",
                           json={"group_jid": "333@g.us", "blueprint_id": "fa"})
        assert resp.status_code == 200

    verify = Session()
    acct = verify.query(UserAccount).filter_by(phone="972500009999", group_jid="333@g.us").first()
    assert acct is not None
    assert acct.role == "owner"  # sole participant -> personal group owner
    verify.close()


@pytest.mark.asyncio
async def test_list_groups_includes_notes(db):
    _seed(db)
    row = db.query(GroupRegistry).filter_by(group_jid="111@g.us").first()
    row.notes = "Kids' allowance tracking"
    db.commit()
    db.close()

    Session = _get_session_factory(db)
    from app.admin import api as admin_api

    async def _empty_bridge_groups():
        return {}

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(admin_api, "_fetch_bridge_groups", _empty_bridge_groups):
        app = _make_app(db)
        client = TestClient(app)
        resp = client.get("/admin/api/groups")
        assert resp.status_code == 200
        assert resp.json()[0]["notes"] == "Kids' allowance tracking"


@pytest.mark.asyncio
async def test_update_group_notes(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.patch("/admin/api/groups/111%40g.us",
                            json={"notes": "Test group for the kids"})
        assert resp.status_code == 200

    verify = Session()
    row = verify.query(GroupRegistry).filter_by(group_jid="111@g.us").first()
    assert row.notes == "Test group for the kids"
    verify.close()


@pytest.mark.asyncio
async def test_update_group_notes_not_found_returns_404(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.patch("/admin/api/groups/nope%40g.us",
                            json={"notes": "whatever"})
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_group_type(db):
    """A registered group's type can be corrected after the fact — e.g. a
    group registered as 'personal' (one reporter) turns out to have a second
    person messaging into it and needs to become 'shared' so
    _sync_participants_to_accounts stops auto-linking private_group_jid for
    every participant (which only makes sense for a true 1:1 group)."""
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.patch("/admin/api/groups/111%40g.us",
                            json={"group_type": "shared"})
        assert resp.status_code == 200

    verify = Session()
    row = verify.query(GroupRegistry).filter_by(group_jid="111@g.us").first()
    assert row.group_type == "shared"
    verify.close()


@pytest.mark.asyncio
async def test_update_group_type_alone_does_not_clear_notes(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        client.patch("/admin/api/groups/111%40g.us", json={"notes": "Kids' group"})
        resp = client.patch("/admin/api/groups/111%40g.us", json={"group_type": "shared"})
        assert resp.status_code == 200

    verify = Session()
    row = verify.query(GroupRegistry).filter_by(group_jid="111@g.us").first()
    assert row.notes == "Kids' group"
    assert row.group_type == "shared"
    verify.close()


def test_update_group_sets_shared_ledger(db):
    seed_blueprint(db, id="family_accounting", display_name="FA")
    seed_group(db, "222@g.us", blueprint_id="family_accounting", group_type="shared")
    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.patch("/admin/api/groups/222%40g.us", json={"shared_ledger": False})
        assert resp.status_code == 200

    verify = Session()
    row = verify.get(GroupRegistry, "222@g.us")
    assert row.shared_ledger is False
    verify.close()


def test_update_group_rejects_shared_ledger_conflict(db):
    from app.db.models import GroupParticipant

    seed_blueprint(db, id="family_accounting", display_name="FA")
    seed_group(db, "existing_shared2@g.us", blueprint_id="family_accounting", group_type="shared", shared_ledger=True)
    db.add(GroupParticipant(group_jid="existing_shared2@g.us", phone="972501110003", status="active"))
    seed_group(db, "personal_grp2@g.us", blueprint_id="family_accounting", group_type="personal", shared_ledger=False)
    db.add(GroupParticipant(group_jid="personal_grp2@g.us", phone="972501110003", status="active"))
    db.commit()

    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch(
            "/admin/api/groups/personal_grp2%40g.us",
            json={"group_type": "shared", "shared_ledger": True},
        )
        assert resp.status_code == 400
        assert "existing_shared2@g.us" in resp.json()["detail"]

    verify = Session()
    row = verify.get(GroupRegistry, "personal_grp2@g.us")
    assert row.group_type == "personal"  # rejected — unchanged
    verify.close()


@pytest.mark.asyncio
async def test_list_unregistered_participants(db):
    """People who joined a registered group before register_group's
    people-sync fix (or approve_registration missed them for any reason)
    have a GroupParticipant row but no UserAccount/AdminNumbers entry —
    this endpoint backs the People-tab "Add person" dropdown that lets an
    admin quickly pick them without knowing their phone number by heart."""
    from app.db.models import GroupParticipant, UserAccount
    _seed(db)
    # Already in People -> must NOT appear
    db.add(GroupParticipant(group_jid="111@g.us", phone="972500001111", push_name="Already Known", status="active"))
    db.add(UserAccount(phone="972500001111", group_jid="111@g.us", role="owner"))
    # Not in People yet -> must appear
    db.add(GroupParticipant(group_jid="111@g.us", phone="972500002222", push_name="Not Yet Added", status="active"))
    # In a group that was never registered -> must NOT appear
    db.add(GroupParticipant(group_jid="unregistered@g.us", phone="972500003333", push_name="Unregistered Group", status="active"))
    # Left the group (status != active) -> must NOT appear
    db.add(GroupParticipant(group_jid="111@g.us", phone="972500004444", push_name="Left Already", status="removed"))
    db.commit()
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)
    from app.admin import api as admin_api

    async def _mock_bridge_groups():
        return {"111@g.us": {"name": "Family Group", "participants": []}}

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(admin_api, "_fetch_bridge_groups", _mock_bridge_groups):
        client = TestClient(app)
        resp = client.get("/admin/api/people/unregistered-participants")
        assert resp.status_code == 200
        data = resp.json()
        phones = {row["phone"] for row in data}
        assert phones == {"972500002222"}
        assert data[0]["name"] == "Not Yet Added"
        assert data[0]["group_jid"] == "111@g.us"
        assert data[0]["group_name"] == "Family Group"


@pytest.mark.asyncio
async def test_list_unregistered_participants_group_name_falls_back_to_jid(db):
    """When the bridge doesn't know about the group (e.g. it's not a real
    live WhatsApp group, or the bridge is unreachable), group_name must
    fall back to the raw JID — same convention as list_groups."""
    from app.db.models import GroupParticipant
    _seed(db)
    db.add(GroupParticipant(group_jid="111@g.us", phone="972500005555", push_name="No Bridge Name", status="active"))
    db.commit()
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)
    from app.admin import api as admin_api

    async def _empty_bridge_groups():
        return {}

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(admin_api, "_fetch_bridge_groups", _empty_bridge_groups):
        client = TestClient(app)
        resp = client.get("/admin/api/people/unregistered-participants")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["group_name"] == "111@g.us"


@pytest.mark.asyncio
async def test_delete_group(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.delete("/admin/api/groups/111%40g.us")
        assert resp.status_code == 200

    verify = Session()
    assert verify.query(GroupRegistry).filter_by(group_jid="111@g.us").first() is None
    verify.close()


# -- /admin/api/admins -------------------------------------------------------

def test_list_admins(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.get("/admin/api/admins")
        assert resp.status_code == 200
        phones = [a["phone_number"] for a in resp.json()]
        assert "972500000001" in phones


def test_add_admin(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.post("/admin/api/admins", json={"phone_number": "972500000099"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    verify = Session()
    assert verify.query(AdminNumbers).filter_by(phone_number="972500000099").first() is not None
    verify.close()


def test_delete_admin(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.delete("/admin/api/admins/972500000001")
        assert resp.status_code == 200

    verify = Session()
    assert verify.query(AdminNumbers).filter_by(phone_number="972500000001").first() is None
    verify.close()


# -- /admin/api/blueprints ---------------------------------------------------

def test_list_blueprints(db):
    _seed(db)
    db.close()

    Session = _get_session_factory(db)
    app = _make_app(db)

    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.get("/admin/api/blueprints")
        assert resp.status_code == 200
        names = [b["display_name"] for b in resp.json()]
        assert "Family Accounting" in names


# -- auth required -----------------------------------------------------------

def test_endpoints_require_auth(db):
    """Without overriding require_auth, all endpoints should return 401."""
    app = _make_app(db, override_auth=False)

    Session = _get_session_factory(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/admin/api/groups").status_code == 401
        assert client.get("/admin/api/admins").status_code == 401
        assert client.get("/admin/api/blueprints").status_code == 401


def test_register_group_duplicate_returns_409(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    Session = _get_session_factory(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/api/groups",
                           json={"group_jid": "111@g.us", "blueprint_id": "fa"})
        assert resp.status_code == 409


def test_add_admin_duplicate_returns_409(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    Session = _get_session_factory(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/api/admins",
                           json={"phone_number": "972500000001"})
        assert resp.status_code == 409


# -- /admin/api/households ---------------------------------------------------

from app.db.models import Household, HouseholdMember


def _seed_household_prereqs(db):
    """Seed blueprint + group so household member FK on private_group_jid can resolve."""
    seed_blueprint(db, id="fa", display_name="Family Accounting")
    seed_group(db, "priv_g@g.us", blueprint_id="fa")


def test_list_households_empty(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        resp = TestClient(app).get("/admin/api/households")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_and_list_household(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        create_resp = client.post("/admin/api/households", json={"name": "Itzkovitch"})
        assert create_resp.status_code == 200
        hid = create_resp.json()["id"]
        assert create_resp.json()["name"] == "Itzkovitch"

        list_resp = client.get("/admin/api/households")
        assert list_resp.status_code == 200
        names = [h["name"] for h in list_resp.json()]
        assert "Itzkovitch" in names
        assert any(h["id"] == hid for h in list_resp.json())


def test_create_household_empty_name_returns_400(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/admin/api/households", json={"name": "  "}
        )
    assert resp.status_code == 400


def test_delete_household_removes_members(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        h = client.post("/admin/api/households", json={"name": "Family"}).json()
        hid = h["id"]
        client.post(f"/admin/api/households/{hid}/members",
                    json={"phone": "972501234567"})
        del_resp = client.delete(f"/admin/api/households/{hid}")
        assert del_resp.status_code == 200

    # Verify household and member are gone
    verify = Session()
    assert verify.query(Household).filter_by(id=hid).first() is None
    assert verify.query(HouseholdMember).filter_by(household_id=hid).count() == 0
    verify.close()


def test_delete_household_not_found(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        resp = TestClient(app, raise_server_exceptions=False).delete(
            "/admin/api/households/nonexistent-id"
        )
    assert resp.status_code == 404


def test_add_household_member(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        hid = client.post("/admin/api/households", json={"name": "F"}).json()["id"]
        resp = client.post(f"/admin/api/households/{hid}/members",
                           json={"phone": "972501234567", "display_name": "Alice"})
        assert resp.status_code == 200
        assert resp.json()["phone"] == "972501234567"
        assert resp.json()["updated"] is False

    verify = Session()
    m = verify.query(HouseholdMember).filter_by(phone="972501234567").first()
    assert m is not None
    assert m.display_name == "Alice"
    assert m.household_id == hid
    verify.close()


def test_add_household_member_idempotent(db):
    """Second add with same phone in same household updates fields, returns updated=True."""
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        hid = client.post("/admin/api/households", json={"name": "F"}).json()["id"]
        client.post(f"/admin/api/households/{hid}/members", json={"phone": "972507777777"})
        resp = client.post(f"/admin/api/households/{hid}/members",
                           json={"phone": "972507777777", "display_name": "Bob"})
        assert resp.status_code == 200
        assert resp.json()["updated"] is True

    verify = Session()
    m = verify.query(HouseholdMember).filter_by(phone="972507777777").first()
    assert m.display_name == "Bob"
    verify.close()


def test_add_household_member_cross_household_conflict_returns_409(db):
    """Adding a phone that belongs to a different household raises 409."""
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        h1 = client.post("/admin/api/households", json={"name": "H1"}).json()["id"]
        h2 = client.post("/admin/api/households", json={"name": "H2"}).json()["id"]
        client.post(f"/admin/api/households/{h1}/members", json={"phone": "972508888888"})
        resp = client.post(f"/admin/api/households/{h2}/members", json={"phone": "972508888888"})
    assert resp.status_code == 409


def test_add_household_member_invalid_phone_returns_400(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        hid = client.post("/admin/api/households", json={"name": "F"}).json()["id"]
        resp = client.post(f"/admin/api/households/{hid}/members", json={"phone": "abc"})
    assert resp.status_code == 400


def test_update_household_member_display_name(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        hid = client.post("/admin/api/households", json={"name": "F"}).json()["id"]
        client.post(f"/admin/api/households/{hid}/members",
                    json={"phone": "972509999999", "display_name": "Old"})
        patch_resp = client.patch(
            f"/admin/api/households/{hid}/members/972509999999",
            json={"display_name": "New"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["display_name"] == "New"


def test_update_household_member_links_private_group_jid(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        hid = client.post("/admin/api/households", json={"name": "F"}).json()["id"]
        client.post(f"/admin/api/households/{hid}/members", json={"phone": "972500011111"})
        patch_resp = client.patch(
            f"/admin/api/households/{hid}/members/972500011111",
            json={"private_group_jid": "priv_g@g.us"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["private_group_jid"] == "priv_g@g.us"


def test_remove_household_member(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        hid = client.post("/admin/api/households", json={"name": "F"}).json()["id"]
        client.post(f"/admin/api/households/{hid}/members", json={"phone": "972506666666"})
        del_resp = client.delete(f"/admin/api/households/{hid}/members/972506666666")
        assert del_resp.status_code == 200

    verify = Session()
    assert verify.query(HouseholdMember).filter_by(phone="972506666666").first() is None
    verify.close()


def test_remove_household_member_not_found_returns_404(db):
    _seed_household_prereqs(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        hid = TestClient(app).post("/admin/api/households", json={"name": "F"}).json()["id"]
        resp = TestClient(app, raise_server_exceptions=False).delete(
            f"/admin/api/households/{hid}/members/972500000000"
        )
    assert resp.status_code == 404


def test_approve_registration_autolinks_household_member(db):
    """approve_registration sets private_group_jid on an existing HouseholdMember."""
    from app.db.models import UserAccount, GroupParticipant
    seed_blueprint(db, id="fa", display_name="FA")
    seed_group(db, "newgrp@g.us", blueprint_id="fa",
               group_type="unregistered", status="active")
    # Seed the participant so approve_registration can discover the phone
    db.add(GroupParticipant(group_jid="newgrp@g.us", phone="972501112223", status="active"))
    h = Household(name="Family")
    db.add(h)
    db.flush()
    m = HouseholdMember(household_id=h.id, phone="972501112223", private_group_jid=None)
    db.add(m)
    db.commit()

    Session = _get_session_factory(db)
    app = _make_app(db)
    from unittest.mock import AsyncMock
    import app.bridge_client as _bc_mod
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(_bc_mod, "send_message", new=AsyncMock()):
        client = TestClient(app)
        resp = client.post(
            "/admin/api/people/pending/newgrp%40g.us/approve",
            json={"group_type": "personal"},
        )
        assert resp.status_code == 200

    verify = Session()
    linked = verify.query(HouseholdMember).filter_by(phone="972501112223").first()
    assert linked is not None
    assert linked.private_group_jid == "newgrp@g.us"
    verify.close()


def test_approve_registration_personal_with_multiple_participants_does_not_autolink(db):
    """Regression: a group registered/approved as group_type='personal' but
    that actually has MORE THAN ONE active participant must never auto-link
    private_group_jid for any of them. _sync_participants_to_accounts already
    computes len(phones) for the owner/member role split one line above the
    auto-link block — this reuses that same fact rather than blindly trusting
    group_type, since an admin can simply forget to reclassify a group from
    'personal' to 'shared' once a second person starts posting in it (the
    documented remediation in test_update_group_type). Confirmed in production:
    a 6-participant invoice_curator group was still labeled 'personal', and
    one participant's HouseholdMember row had private_group_jid silently
    pointed at that shared group — which made resolve_inbound's household_member
    lookup (checked before the LID-safe known_lid lookup) hijack every message
    in that group to the wrong phone, including the real admin's."""
    from app.db.models import UserAccount, GroupParticipant
    seed_blueprint(db, id="fa", display_name="FA")
    seed_group(db, "sharedbutlabeledpersonal@g.us", blueprint_id="fa",
               group_type="unregistered", status="active")
    db.add(GroupParticipant(group_jid="sharedbutlabeledpersonal@g.us", phone="972501112223", status="active"))
    db.add(GroupParticipant(group_jid="sharedbutlabeledpersonal@g.us", phone="972501112224", status="active"))
    h = Household(name="Family")
    db.add(h)
    db.flush()
    m = HouseholdMember(household_id=h.id, phone="972501112223", private_group_jid=None)
    db.add(m)
    db.commit()

    Session = _get_session_factory(db)
    app = _make_app(db)
    from unittest.mock import AsyncMock
    import app.bridge_client as _bc_mod
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(_bc_mod, "send_message", new=AsyncMock()):
        client = TestClient(app)
        resp = client.post(
            "/admin/api/people/pending/sharedbutlabeledpersonal%40g.us/approve",
            json={"group_type": "personal"},
        )
        assert resp.status_code == 200

    verify = Session()
    linked = verify.query(HouseholdMember).filter_by(phone="972501112223").first()
    assert linked.private_group_jid is None
    verify.close()


def test_approve_registration_shared_sets_shared_ledger_default_on(db):
    from app.db.models import GroupParticipant
    seed_blueprint(db, id="fa", display_name="FA")
    seed_group(db, "sharedgrp@g.us", blueprint_id="fa", group_type="unregistered", status="active")
    db.add(GroupParticipant(group_jid="sharedgrp@g.us", phone="972501110001", status="active"))
    db.commit()

    Session = _get_session_factory(db)
    app = _make_app(db)
    from unittest.mock import AsyncMock
    import app.bridge_client as _bc_mod
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(_bc_mod, "send_message", new=AsyncMock()):
        client = TestClient(app)
        resp = client.post(
            "/admin/api/people/pending/sharedgrp%40g.us/approve",
            json={"group_type": "shared"},
        )
        assert resp.status_code == 200

    verify = Session()
    row = verify.get(GroupRegistry, "sharedgrp@g.us")
    assert row.group_type == "shared"
    assert row.shared_ledger is True
    verify.close()


def test_approve_registration_shared_rejects_conflicting_member(db):
    """A participant already pooled in another shared-ledger group can't be
    approved into a second one."""
    from app.db.models import GroupParticipant

    seed_blueprint(db, id="family_accounting", display_name="FA")
    seed_group(db, "existing_shared@g.us", blueprint_id="family_accounting", group_type="shared", shared_ledger=True)
    db.add(GroupParticipant(group_jid="existing_shared@g.us", phone="972501110002", status="active"))

    seed_group(db, "newpending@g.us", blueprint_id="family_accounting", group_type="unregistered", status="active")
    db.add(GroupParticipant(group_jid="newpending@g.us", phone="972501110002", status="active"))
    db.commit()

    Session = _get_session_factory(db)
    app = _make_app(db)
    from unittest.mock import AsyncMock
    import app.bridge_client as _bc_mod
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)), \
         patch.object(_bc_mod, "send_message", new=AsyncMock()):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/admin/api/people/pending/newpending%40g.us/approve",
            json={"group_type": "shared"},
        )
        assert resp.status_code == 400
        assert "existing_shared@g.us" in resp.json()["detail"]

    verify = Session()
    row = verify.get(GroupRegistry, "newpending@g.us")
    assert row.group_type == "unregistered"  # rejected — unchanged
    verify.close()


def test_patch_person_sets_known_lid(db):
    _seed(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        resp = client.patch(
            "/admin/api/people/972500000099",
            json={"known_lid": "175715853041683"},
        )
        assert resp.status_code == 200

    from app.db.models import UserProfile
    verify = Session()
    profile = verify.query(UserProfile).filter_by(phone="972500000099").first()
    assert profile is not None
    assert profile.known_lid == "175715853041683"
    verify.close()


def test_patch_person_clears_known_lid(db):
    _seed(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app)
        client.patch("/admin/api/people/972500000099", json={"known_lid": "12345"})
        resp = client.patch("/admin/api/people/972500000099", json={"known_lid": ""})
        assert resp.status_code == 200

    from app.db.models import UserProfile
    verify = Session()
    profile = verify.query(UserProfile).filter_by(phone="972500000099").first()
    assert profile.known_lid is None
    verify.close()


def test_patch_person_known_lid_conflict_returns_clean_error(db):
    _seed(db)
    Session = _get_session_factory(db)
    app = _make_app(db)
    with patch("app.admin.api.SessionLocal", side_effect=lambda: SessionCM(Session)):
        client = TestClient(app, raise_server_exceptions=False)
        r1 = client.patch("/admin/api/people/972500000097", json={"known_lid": "666000111"})
        assert r1.status_code == 200
        r2 = client.patch("/admin/api/people/972500000098", json={"known_lid": "666000111"})
        assert r2.status_code == 400
        assert "already" in r2.json()["detail"].lower()


# -- internal helper ---------------------------------------------------------

def _get_session_factory(db):
    """Return a sessionmaker bound to the same engine as ``db``."""
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=db.get_bind())
