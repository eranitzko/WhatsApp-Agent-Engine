# Email Allowlist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `REPORT_EMAIL_ALLOWLIST` env var with a DB-backed allowlist table; expose CRUD via API; auto-sync from the People panel; add a Settings sub-panel in the admin UI.

**Architecture:** New `email_allowlist` table (migration 013 + ORM model) is the single source of truth. `send_email_tool._is_allowed()` reads from it (empty = allow all). `PATCH /people/{phone}` auto-upserts/removes from the allowlist when a person's email changes. Three new API endpoints under `/settings/email-allowlist` handle manual management. The Settings page gains an "Email Allowlist" sub-panel.

**Tech Stack:** Python/FastAPI, SQLAlchemy (sync), Alembic, vanilla JS (admin SPA)

---

## File Map

| File | Change |
|---|---|
| `orchestrator/app/db/migrations/versions/013_email_allowlist.py` | **Create** — migration |
| `orchestrator/app/db/models.py` | **Modify** — add `EmailAllowlist` ORM model |
| `orchestrator/app/tools/send_email_tool.py` | **Modify** — `_is_allowed()` reads from DB |
| `orchestrator/app/admin/api.py` | **Modify** — email in `GET /people`, auto-sync in `patch_person`, 3 new endpoints |
| `orchestrator/app/static/admin/app.js` | **Modify** — allowlist sub-panel in Settings page |
| `orchestrator/tests/test_email_allowlist.py` | **Create** — tests |

---

### Task 1: Migration 013 + ORM model

**Files:**
- Create: `orchestrator/app/db/migrations/versions/013_email_allowlist.py`
- Modify: `orchestrator/app/db/models.py`
- Test: `orchestrator/tests/test_email_allowlist.py`

- [ ] **Step 1: Create migration file**

Create `orchestrator/app/db/migrations/versions/013_email_allowlist.py`:

```python
"""Add email_allowlist table

Revision ID: 013
Revises: 012
Create Date: 2026-06-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_allowlist",
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("email_allowlist")
```

- [ ] **Step 2: Add `EmailAllowlist` ORM model**

Open `orchestrator/app/db/models.py`. Add this class after the existing `UserProfile` class:

```python
class EmailAllowlist(Base):
    __tablename__ = "email_allowlist"

    email        = Column(String, primary_key=True)
    display_name = Column(String, nullable=True)
    created_at   = Column(DateTime(timezone=True), nullable=False,
                          server_default=sa.func.now())
```

Make sure `import sqlalchemy as sa` is already present at the top of `models.py` (it should be).

- [ ] **Step 3: Write failing test**

Create `orchestrator/tests/test_email_allowlist.py`:

```python
"""Tests for email allowlist: ORM, API endpoints, and send_email _is_allowed."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin.api import router as api_router
from app.admin.auth import require_auth
from app.db.models import EmailAllowlist


def _make_app(db):
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None
    return app


def _session_factory(db):
    from unittest.mock import patch as _patch
    from app.db import session as _sess_mod

    class _CM:
        def __enter__(self): return db
        def __exit__(self, *a): db.flush()

    return lambda: _CM()


# ── ORM ──────────────────────────────────────────────────────────────────────

def test_email_allowlist_model_created(db):
    db.add(EmailAllowlist(email="test@example.com", display_name="Test User"))
    db.commit()
    row = db.query(EmailAllowlist).filter_by(email="test@example.com").first()
    assert row is not None
    assert row.display_name == "Test User"
```

- [ ] **Step 4: Run test to verify it passes (model only)**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine\orchestrator"
pytest tests/test_email_allowlist.py::test_email_allowlist_model_created -v
```

Expected: PASS — the `db` fixture creates tables from `Base.metadata`, which now includes `email_allowlist`.

- [ ] **Step 5: Commit**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine"
git add orchestrator/app/db/migrations/versions/013_email_allowlist.py orchestrator/app/db/models.py orchestrator/tests/test_email_allowlist.py
git commit -m "feat: add email_allowlist table — migration 013 + ORM model"
```

---

### Task 2: Update `_is_allowed()` in `send_email_tool.py`

**Files:**
- Modify: `orchestrator/app/tools/send_email_tool.py`
- Test: `orchestrator/tests/test_email_allowlist.py`

- [ ] **Step 1: Write failing tests**

Add to `orchestrator/tests/test_email_allowlist.py`:

```python
from unittest.mock import patch as _mock_patch
from app.db.models import EmailAllowlist


def _patch_session(db):
    """Return a context manager that patches SessionLocal to return `db`."""
    from app.db import session as sess_mod

    class _CM:
        def __enter__(self): return db
        def __exit__(self, *a): db.flush()

    return _mock_patch.object(sess_mod, "SessionLocal", return_value=_CM())


def test_is_allowed_empty_table_permits_any(db):
    """Empty allowlist → any address is allowed."""
    with _patch_session(db):
        from app.tools.send_email_tool import _is_allowed
        assert _is_allowed("anyone@anywhere.com", db=db) is True


def test_is_allowed_blocks_unlisted(db):
    db.add(EmailAllowlist(email="boss@company.com", display_name="Boss"))
    db.commit()
    from app.tools.send_email_tool import _is_allowed
    assert _is_allowed("stranger@evil.com", db=db) is False


def test_is_allowed_permits_listed(db):
    db.add(EmailAllowlist(email="boss@company.com", display_name="Boss"))
    db.commit()
    from app.tools.send_email_tool import _is_allowed
    assert _is_allowed("boss@company.com", db=db) is True


def test_is_allowed_case_insensitive(db):
    db.add(EmailAllowlist(email="boss@company.com"))
    db.commit()
    from app.tools.send_email_tool import _is_allowed
    assert _is_allowed("BOSS@COMPANY.COM", db=db) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine\orchestrator"
pytest tests/test_email_allowlist.py::test_is_allowed_empty_table_permits_any tests/test_email_allowlist.py::test_is_allowed_blocks_unlisted -v
```

Expected: FAIL — `_is_allowed` currently takes no `db` argument.

- [ ] **Step 3: Rewrite `_is_allowed()` in `send_email_tool.py`**

Replace the existing `_is_allowed` function (currently reads from env var) with:

```python
def _is_allowed(to: str, db=None) -> bool:
    """Return True if `to` is permitted to receive emails.

    Reads from the email_allowlist DB table.
    If the table is empty, any address is allowed (open by default).
    The `db` parameter is injected in tests; production uses a fresh session.
    """
    from app.db.models import EmailAllowlist

    def _check(session):
        count = session.query(EmailAllowlist).count()
        if count == 0:
            return True
        return (
            session.query(EmailAllowlist)
            .filter(EmailAllowlist.email == to.strip().lower())
            .first()
        ) is not None

    if db is not None:
        return _check(db)

    from app.db.session import SessionLocal
    with SessionLocal() as session:
        return _check(session)
```

Also update the call site in `_exec_send_email` — the existing check `if not _is_allowed(to):` stays unchanged (no `db` argument needed in production).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine\orchestrator"
pytest tests/test_email_allowlist.py::test_is_allowed_empty_table_permits_any tests/test_email_allowlist.py::test_is_allowed_blocks_unlisted tests/test_email_allowlist.py::test_is_allowed_permits_listed tests/test_email_allowlist.py::test_is_allowed_case_insensitive -v
```

Expected: all PASS.

- [ ] **Step 5: Run full test suite to check no regressions**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine\orchestrator"
pytest --tb=short -q
```

Expected: same pass count as before; no new failures.

- [ ] **Step 6: Commit**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine"
git add orchestrator/app/tools/send_email_tool.py orchestrator/tests/test_email_allowlist.py
git commit -m "feat: _is_allowed reads from email_allowlist DB table instead of env var"
```

---

### Task 3: API — email in GET /people, allowlist CRUD, auto-sync in patch_person

**Files:**
- Modify: `orchestrator/app/admin/api.py`
- Test: `orchestrator/tests/test_email_allowlist.py`

- [ ] **Step 1: Write failing tests**

Add to `orchestrator/tests/test_email_allowlist.py`:

```python
from app.db.models import AdminNumbers, UserProfile, UserAccount, EmailAllowlist
import json


def _seed_person(db, phone="972501234567", display_name="Alice", email=None):
    profile = UserProfile(phone=phone, display_name=display_name)
    if email:
        profile.email = email
    db.add(profile)
    db.commit()
    return profile


def _make_client(db):
    from app.db import session as sess_mod
    from unittest.mock import patch as _p

    class _CM:
        def __enter__(self): return db
        def __exit__(self, *a): db.flush()

    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None
    with _p.object(sess_mod, "SessionLocal", return_value=_CM()):
        client = TestClient(app)
    return client, _CM


def test_get_people_includes_email(db):
    _seed_person(db, email="alice@example.com")
    db.add(AdminNumbers(phone_number="972501234567"))
    db.commit()

    from app.db import session as sess_mod
    from unittest.mock import patch as _p

    class _CM:
        def __enter__(self): return db
        def __exit__(self, *a): db.flush()

    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with _p.object(sess_mod, "SessionLocal", return_value=_CM()):
        client = TestClient(app)
        resp = client.get("/admin/api/people")

    assert resp.status_code == 200
    people = resp.json()
    assert len(people) == 1
    assert people[0]["email"] == "alice@example.com"


def test_allowlist_crud(db):
    from app.db import session as sess_mod
    from unittest.mock import patch as _p

    class _CM:
        def __enter__(self): return db
        def __exit__(self, *a): db.flush()

    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with _p.object(sess_mod, "SessionLocal", return_value=_CM()):
        client = TestClient(app)

        # GET empty
        resp = client.get("/admin/api/settings/email-allowlist")
        assert resp.status_code == 200
        assert resp.json() == []

        # POST add
        resp = client.post("/admin/api/settings/email-allowlist",
                           json={"email": "boss@company.com", "display_name": "Boss"})
        assert resp.status_code == 200

        # GET shows entry
        resp = client.get("/admin/api/settings/email-allowlist")
        assert len(resp.json()) == 1
        assert resp.json()[0]["email"] == "boss@company.com"
        assert resp.json()[0]["display_name"] == "Boss"

        # POST duplicate → 409
        resp = client.post("/admin/api/settings/email-allowlist",
                           json={"email": "boss@company.com"})
        assert resp.status_code == 409

        # DELETE
        resp = client.delete("/admin/api/settings/email-allowlist/boss@company.com")
        assert resp.status_code == 200
        assert db.query(EmailAllowlist).count() == 0

        # DELETE non-existent → 404
        resp = client.delete("/admin/api/settings/email-allowlist/ghost@company.com")
        assert resp.status_code == 404


def test_patch_person_email_syncs_to_allowlist(db):
    _seed_person(db, display_name="Alice")
    from app.db import session as sess_mod
    from unittest.mock import patch as _p

    class _CM:
        def __enter__(self): return db
        def __exit__(self, *a): db.flush()

    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with _p.object(sess_mod, "SessionLocal", return_value=_CM()):
        client = TestClient(app)

        # Set email → should appear in allowlist
        resp = client.patch("/admin/api/people/972501234567",
                            json={"email": "alice@example.com"})
        assert resp.status_code == 200
        row = db.query(EmailAllowlist).filter_by(email="alice@example.com").first()
        assert row is not None
        assert row.display_name == "Alice"

        # Clear email → should be removed from allowlist
        resp = client.patch("/admin/api/people/972501234567",
                            json={"email": ""})
        assert resp.status_code == 200
        row = db.query(EmailAllowlist).filter_by(email="alice@example.com").first()
        assert row is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine\orchestrator"
pytest tests/test_email_allowlist.py::test_get_people_includes_email tests/test_email_allowlist.py::test_allowlist_crud tests/test_email_allowlist.py::test_patch_person_email_syncs_to_allowlist -v
```

Expected: FAIL — endpoints don't exist yet, `email` not in `GET /people` response.

- [ ] **Step 3: Update `GET /people` to include email**

In `orchestrator/app/admin/api.py`, find the `list_people` function. The `result.append({...})` block currently does not include `email`. Add it:

```python
result.append({
    "phone": phone,
    "display_name": profile.display_name if profile else None,
    "email": profile.email if profile else None,          # ← add this line
    "is_admin": admin is not None,
    "admin_label": admin.label if admin else None,
    "group_jid": owner_acct.group_jid if owner_acct else None,
    "role": owner_acct.role if owner_acct else None,
    "group_type": grp.group_type if grp else None,
    "created_at": owner_acct.created_at.isoformat() if owner_acct and owner_acct.created_at else None,
})
```

- [ ] **Step 4: Add three allowlist endpoints**

In `orchestrator/app/admin/api.py`, after the Settings section (around line 575), add:

```python
# -- Email Allowlist ---------------------------------------------------------

class AddAllowlistRequest(BaseModel):
    email: str
    display_name: str | None = None


@router.get("/settings/email-allowlist", dependencies=[Depends(require_auth)])
def list_email_allowlist():
    from app.db.models import EmailAllowlist
    with SessionLocal() as db:
        rows = db.query(EmailAllowlist).order_by(EmailAllowlist.created_at).all()
        return [
            {
                "email": r.email,
                "display_name": r.display_name,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


@router.post("/settings/email-allowlist", dependencies=[Depends(require_auth)])
def add_email_allowlist(body: AddAllowlistRequest):
    from app.db.models import EmailAllowlist
    email = body.email.strip().lower()
    with SessionLocal() as db:
        if db.get(EmailAllowlist, email):
            raise HTTPException(status_code=409, detail="Email already in allowlist")
        db.add(EmailAllowlist(email=email, display_name=body.display_name or None))
        db.commit()
    return {"ok": True}


@router.delete("/settings/email-allowlist/{email:path}", dependencies=[Depends(require_auth)])
def delete_email_allowlist(email: str):
    from app.db.models import EmailAllowlist
    with SessionLocal() as db:
        row = db.get(EmailAllowlist, email.strip().lower())
        if not row:
            raise HTTPException(status_code=404, detail="Email not in allowlist")
        db.delete(row)
        db.commit()
    return {"ok": True}
```

- [ ] **Step 5: Add auto-sync to `patch_person`**

In `orchestrator/app/admin/api.py`, find the `patch_person` function. Add the import at the top of the function body and the auto-sync logic inside the `if body.email is not None` branch.

Current code:
```python
@router.patch("/people/{phone}")
def patch_person(phone: str, body: UpdatePersonFullRequest, _=Depends(require_auth)):
    with SessionLocal() as db:
        if body.display_name is not None or body.email is not None:
            profile = db.query(UserProfile).filter_by(phone=phone).first()
            if profile is None:
                profile = UserProfile(phone=phone)
                db.add(profile)
            if body.display_name is not None:
                profile.display_name = body.display_name
            if body.email is not None:
                profile.email = body.email
```

Replace the inner block with:
```python
@router.patch("/people/{phone}")
def patch_person(phone: str, body: UpdatePersonFullRequest, _=Depends(require_auth)):
    from app.db.models import EmailAllowlist
    with SessionLocal() as db:
        if body.display_name is not None or body.email is not None:
            profile = db.query(UserProfile).filter_by(phone=phone).first()
            if profile is None:
                profile = UserProfile(phone=phone)
                db.add(profile)

            old_email = profile.email  # capture before mutation

            if body.display_name is not None:
                profile.display_name = body.display_name
            if body.email is not None:
                profile.email = body.email or None  # empty string → None

            # Auto-sync email allowlist
            if body.email is not None:
                new_email = (body.email or "").strip().lower()
                old_email_norm = (old_email or "").strip().lower()

                # Remove old email from allowlist if it changed
                if old_email_norm and old_email_norm != new_email:
                    old_row = db.get(EmailAllowlist, old_email_norm)
                    if old_row:
                        db.delete(old_row)

                if new_email:
                    # Upsert new email
                    display = (
                        body.display_name
                        or (profile.display_name if profile else None)
                        or phone
                    )
                    existing = db.get(EmailAllowlist, new_email)
                    if existing:
                        existing.display_name = display
                    else:
                        db.add(EmailAllowlist(email=new_email, display_name=display))
                else:
                    # Email cleared — remove from allowlist
                    if old_email_norm:
                        old_row = db.get(EmailAllowlist, old_email_norm)
                        if old_row:
                            db.delete(old_row)
```

Keep the rest of `patch_person` unchanged (the `is_admin` block and `db.commit()`).

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine\orchestrator"
pytest tests/test_email_allowlist.py -v
```

Expected: all PASS.

- [ ] **Step 7: Run full suite**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine\orchestrator"
pytest --tb=short -q
```

Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine"
git add orchestrator/app/admin/api.py orchestrator/tests/test_email_allowlist.py
git commit -m "feat: email allowlist API — GET /people email, CRUD endpoints, auto-sync from People"
```

---

### Task 4: UI — Settings allowlist sub-panel

**Files:**
- Modify: `orchestrator/app/static/admin/app.js`

No unit tests for vanilla JS — manual verification steps are provided.

- [ ] **Step 1: Find the `renderSettings` function**

Open `orchestrator/app/static/admin/app.js` and locate the `renderSettings` async function.

- [ ] **Step 2: Add allowlist section to `renderSettings`**

Inside `renderSettings`, after the existing settings form HTML is built and inserted into `app.innerHTML`, append a call to a new helper. The pattern is: render the settings form first, then call `renderAllowlistSection()` to populate the allowlist sub-panel below it.

Find the line(s) where `renderSettings` sets `app.innerHTML`. After the existing form content string, append this placeholder section to the HTML string:

```html
<div id="allowlist-section" style="margin-top:32px"></div>
```

Then at the end of `renderSettings` (after `app.innerHTML = ...`), add:

```javascript
await renderAllowlistSection();
```

- [ ] **Step 3: Add `renderAllowlistSection` function**

Add this new function to `app.js` (anywhere outside of `renderSettings`, e.g. just below it):

```javascript
async function renderAllowlistSection() {
  const section = document.getElementById('allowlist-section');
  if (!section) return;

  const entries = await apiFetch('/settings/email-allowlist');

  const rows = entries.length === 0
    ? `<tr><td colspan="3" class="empty">No addresses — all recipients are permitted.</td></tr>`
    : entries.map(e => `
        <tr>
          <td>${escHtml(e.display_name || '—')}</td>
          <td>${escHtml(e.email)}</td>
          <td style="white-space:nowrap">
            <button class="btn btn-danger" onclick="removeAllowlistEntry('${escAttr(e.email)}')">✕</button>
          </td>
        </tr>`).join('');

  section.innerHTML = `
    <h3 style="margin:0 0 12px;font-size:15px">Email Allowlist</h3>
    <div class="card" style="padding:0;overflow:hidden">
      <table class="table">
        <thead>
          <tr>
            <th>Display Name</th>
            <th>Email</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="add-row" style="padding:12px;border-top:1px solid var(--border)">
        <input id="al-name" type="text" placeholder="Display name (optional)" style="flex:1;min-width:0">
        <input id="al-email" type="email" placeholder="Email address" style="flex:1.5;min-width:0">
        <button class="btn btn-primary" onclick="addAllowlistEntry()">Add</button>
      </div>
    </div>`;
}

async function addAllowlistEntry() {
  const email = document.getElementById('al-email').value.trim();
  const display_name = document.getElementById('al-name').value.trim() || null;
  if (!email) return;
  await apiFetch('/settings/email-allowlist', {
    method: 'POST',
    body: JSON.stringify({ email, display_name }),
  });
  await renderAllowlistSection();
}

async function removeAllowlistEntry(email) {
  if (!confirm(`Remove ${email} from the allowlist?`)) return;
  await apiFetch('/settings/email-allowlist/' + encodeURIComponent(email), { method: 'DELETE' });
  await renderAllowlistSection();
}
```

Note: `escHtml` and `escAttr` should already be defined in `app.js` (used throughout for XSS-safe rendering). If `escHtml` doesn't exist but `escAttr` does, use `escAttr` for both, or add:

```javascript
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
```

- [ ] **Step 4: Manual verification**

1. Open `http://178.105.63.248:8080/admin` (after deploy) → Settings page
2. Confirm "Email Allowlist" section appears below the existing settings form
3. Add an entry: enter display name "Test" and email "test@example.com" → click Add
4. Confirm the row appears in the table
5. Click ✕ → confirm the row is removed
6. Confirm empty state message appears when list is empty

- [ ] **Step 5: Commit**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine"
git add orchestrator/app/static/admin/app.js
git commit -m "feat: email allowlist sub-panel in admin Settings page"
```

---

### Task 5: Push and deploy

- [ ] **Step 1: Push to GitHub**

```bash
cd "G:\My Drive\Software Projects\WhatsApp Agent Engine"
git push
```

- [ ] **Step 2: Deploy to Hetzner**

```bash
ssh -i "C:\Users\Eranitzkovitch\.ssh\hetzner_ta125" -o StrictHostKeyChecking=no root@178.105.63.248 "cd /opt/whatsapp && git pull && docker compose up --build -d 2>&1"
```

Expected: containers rebuild and restart cleanly. Migration 013 runs automatically on startup.

- [ ] **Step 3: Smoke-test the live server**

1. Open `http://178.105.63.248:8080/admin` → Settings → confirm "Email Allowlist" section visible
2. Add an entry → confirm it appears
3. Open People → edit a person → set their email → confirm the allowlist auto-updates
4. Remove the email from the person → confirm removed from allowlist

---

## Self-Review

**Spec coverage:**
- ✅ DB table `email_allowlist` — Task 1
- ✅ `_is_allowed()` reads from DB — Task 2
- ✅ Empty table = allow all — Task 2 (test: `test_is_allowed_empty_table_permits_any`)
- ✅ `REPORT_EMAIL_ALLOWLIST` env var deprecated — Task 2 (new `_is_allowed` ignores it)
- ✅ `GET /people` returns email — Task 3 Step 3
- ✅ Auto-sync on People email set — Task 3 Step 5
- ✅ Auto-sync on People email cleared — Task 3 Step 5
- ✅ `GET /settings/email-allowlist` — Task 3 Step 4
- ✅ `POST /settings/email-allowlist` — Task 3 Step 4
- ✅ `DELETE /settings/email-allowlist/{email}` — Task 3 Step 4
- ✅ Settings sub-panel with add/remove and display name — Task 4

**Placeholder scan:** No TBDs, no "implement later", all code blocks are complete.

**Type consistency:**
- `EmailAllowlist` defined in Task 1, imported via `from app.db.models import EmailAllowlist` consistently in Tasks 2, 3.
- `_is_allowed(to, db=None)` signature defined in Task 2; existing call site `_is_allowed(to)` needs no change.
- `addAllowlistEntry` / `removeAllowlistEntry` / `renderAllowlistSection` JS functions defined and called consistently in Task 4.
