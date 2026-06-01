# Admin Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a password-protected web admin panel at `/admin` for managing groups, admins, and blueprints.

**Architecture:** A new `app/admin/` module with `auth.py` (JWT), `api.py` (REST endpoints), and `router.py` (static + API mount). Plain HTML/JS frontend in `app/static/admin/`. Mounted onto the existing FastAPI app in `main.py` with no Docker changes.

**Tech Stack:** FastAPI, SQLAlchemy, python-jose[cryptography], httpx, vanilla JS frontend.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `orchestrator/requirements.txt` | Modify | Add `python-jose[cryptography]` |
| `orchestrator/app/config.py` | Modify | Add `admin_ui_password: str = ""` |
| `orchestrator/app/admin/__init__.py` | Create | Empty package marker |
| `orchestrator/app/admin/auth.py` | Create | Password verify, JWT issue/verify, FastAPI dependency |
| `orchestrator/app/admin/api.py` | Create | All `/admin/api/*` REST endpoints |
| `orchestrator/app/admin/router.py` | Create | Mounts static files, includes API router |
| `orchestrator/app/static/admin/index.html` | Create | SPA shell with login + sidebar layout |
| `orchestrator/app/static/admin/app.js` | Create | All rendering, routing, API calls |
| `orchestrator/app/static/admin/style.css` | Create | Dark theme styles |
| `orchestrator/app/main.py` | Modify | Mount admin router |
| `orchestrator/tests/test_admin_auth.py` | Create | Auth unit tests |
| `orchestrator/tests/test_admin_api.py` | Create | API endpoint tests |

---

## Task 1: Dependencies and Config

**Files:**
- Modify: `orchestrator/requirements.txt`
- Modify: `orchestrator/app/config.py`

- [ ] **Step 1: Add python-jose to requirements.txt**

Open `orchestrator/requirements.txt` and add after the `apscheduler` line:

```
# Admin panel auth
python-jose[cryptography]==3.3.0
```

- [ ] **Step 2: Add ADMIN_UI_PASSWORD to config.py**

In `orchestrator/app/config.py`, add inside the `Settings` class after `notion_tasks_database_id`:

```python
    # Admin panel
    admin_ui_password: str = ""
```

- [ ] **Step 3: Install and verify**

```bash
cd orchestrator
pip install python-jose[cryptography]==3.3.0
python -c "from jose import jwt; print('jose ok')"
```
Expected: `jose ok`

- [ ] **Step 4: Commit**

```bash
git add orchestrator/requirements.txt orchestrator/app/config.py
git commit -m "chore: add python-jose dependency and admin_ui_password config"
```

---

## Task 2: Auth Module

**Files:**
- Create: `orchestrator/app/admin/__init__.py`
- Create: `orchestrator/app/admin/auth.py`
- Create: `orchestrator/tests/test_admin_auth.py`

- [ ] **Step 1: Create package marker**

Create `orchestrator/app/admin/__init__.py` as an empty file.

- [ ] **Step 2: Write failing tests**

Create `orchestrator/tests/test_admin_auth.py`:

```python
"""Tests for admin panel auth — password verification and JWT lifecycle."""

import time
import pytest
from unittest.mock import patch
from app.admin.auth import create_token, verify_token, AdminAuthError


def test_create_token_returns_string():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        token = create_token("secret123")
    assert isinstance(token, str)
    assert len(token) > 20


def test_create_token_wrong_password_raises():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        with pytest.raises(AdminAuthError):
            create_token("wrongpassword")


def test_create_token_empty_password_configured_raises():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = ""
        with pytest.raises(AdminAuthError):
            create_token("anything")


def test_verify_token_valid():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        token = create_token("secret123")
        assert verify_token(token) is True


def test_verify_token_tampered_raises():
    with patch("app.admin.auth.settings") as mock_settings:
        mock_settings.admin_ui_password = "secret123"
        token = create_token("secret123")
    # Tamper with the token
    tampered = token[:-4] + "XXXX"
    assert verify_token(tampered) is False


def test_verify_token_empty_string_returns_false():
    assert verify_token("") is False
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd orchestrator
python -m pytest tests/test_admin_auth.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.admin.auth'`

- [ ] **Step 4: Implement auth.py**

Create `orchestrator/app/admin/auth.py`:

```python
"""Admin panel authentication — password check and JWT lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_SECONDS = 86400  # 24 hours

_bearer = HTTPBearer(auto_error=False)


class AdminAuthError(Exception):
    """Raised when password is wrong or admin UI is not configured."""


def _jwt_secret() -> str:
    """Derive a stable JWT signing secret from the admin password."""
    return hashlib.sha256(settings.admin_ui_password.encode()).hexdigest()


def create_token(password: str) -> str:
    """Verify password and return a signed JWT. Raises AdminAuthError on failure."""
    if not settings.admin_ui_password:
        raise AdminAuthError("ADMIN_UI_PASSWORD is not configured")
    if not hmac.compare_digest(password, settings.admin_ui_password):
        raise AdminAuthError("Invalid password")
    import time
    payload = {"sub": "admin", "exp": int(time.time()) + _TOKEN_EXPIRE_SECONDS}
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGORITHM)


def verify_token(token: str) -> bool:
    """Return True if the token is valid and not expired, False otherwise."""
    if not token:
        return False
    try:
        jwt.decode(token, _jwt_secret(), algorithms=[_ALGORITHM])
        return True
    except JWTError:
        return False


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """FastAPI dependency — raises 401 if token is missing or invalid."""
    token = credentials.credentials if credentials else ""
    if not verify_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd orchestrator
python -m pytest tests/test_admin_auth.py -v
```
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/admin/ orchestrator/tests/test_admin_auth.py
git commit -m "feat: admin auth module — password verify and JWT lifecycle"
```

---

## Task 3: API Endpoints

**Files:**
- Create: `orchestrator/app/admin/api.py`
- Create: `orchestrator/tests/test_admin_api.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_admin_api.py`:

```python
"""Tests for admin panel REST API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.admin.api import router as api_router
from app.db.models import Blueprint, GroupRegistry, AdminNumbers


# ── helpers ──────────────────────────────────────────────────────────────────

class _CM:
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *a):
        pass


def _seed(db):
    db.add(Blueprint(id="fa", display_name="Family Accounting",
                     system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="111@g.us", blueprint_id="fa"))
    db.add(AdminNumbers(phone_number="972500000001"))
    db.commit()


def _make_client(db):
    """Build a TestClient with auth bypassed and DB patched."""
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")

    # Bypass auth dependency
    from app.admin.auth import require_auth
    app.dependency_overrides[require_auth] = lambda: None

    with patch("app.admin.api.SessionLocal", return_value=_CM(db)):
        client = TestClient(app, raise_server_exceptions=True)
        yield client


# ── /admin/api/groups ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_groups(db):
    _seed(db)
    with patch("app.admin.api.SessionLocal", return_value=_CM(db)), \
         patch("app.admin.api.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"groups": [{"jid": "111@g.us", "name": "Test Group"}]}
        ))

        app = FastAPI()
        from app.admin.auth import require_auth
        app.include_router(api_router, prefix="/admin/api")
        app.dependency_overrides[require_auth] = lambda: None

        client = TestClient(app)
        resp = client.get("/admin/api/groups")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["group_jid"] == "111@g.us"
        assert data[0]["group_name"] == "Test Group"
        assert data[0]["blueprint_name"] == "Family Accounting"


@pytest.mark.asyncio
async def test_register_group(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with patch("app.admin.api.SessionLocal", return_value=_CM(db)):
        client = TestClient(app)
        resp = client.post("/admin/api/groups",
                           json={"group_jid": "222@g.us", "blueprint_id": "fa"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    row = db.query(GroupRegistry).filter_by(group_jid="222@g.us").first()
    assert row is not None
    assert row.blueprint_id == "fa"


@pytest.mark.asyncio
async def test_delete_group(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with patch("app.admin.api.SessionLocal", return_value=_CM(db)):
        client = TestClient(app)
        resp = client.delete("/admin/api/groups/111%40g.us")
        assert resp.status_code == 200

    assert db.query(GroupRegistry).filter_by(group_jid="111@g.us").first() is None


# ── /admin/api/admins ─────────────────────────────────────────────────────────

def test_list_admins(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with patch("app.admin.api.SessionLocal", return_value=_CM(db)):
        client = TestClient(app)
        resp = client.get("/admin/api/admins")
        assert resp.status_code == 200
        phones = [a["phone_number"] for a in resp.json()]
        assert "972500000001" in phones


def test_add_admin(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with patch("app.admin.api.SessionLocal", return_value=_CM(db)):
        client = TestClient(app)
        resp = client.post("/admin/api/admins", json={"phone_number": "972500000099"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    assert db.query(AdminNumbers).filter_by(phone_number="972500000099").first() is not None


def test_delete_admin(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with patch("app.admin.api.SessionLocal", return_value=_CM(db)):
        client = TestClient(app)
        resp = client.delete("/admin/api/admins/972500000001")
        assert resp.status_code == 200

    assert db.query(AdminNumbers).filter_by(phone_number="972500000001").first() is None


# ── /admin/api/blueprints ─────────────────────────────────────────────────────

def test_list_blueprints(db):
    _seed(db)
    app = FastAPI()
    from app.admin.auth import require_auth
    app.include_router(api_router, prefix="/admin/api")
    app.dependency_overrides[require_auth] = lambda: None

    with patch("app.admin.api.SessionLocal", return_value=_CM(db)):
        client = TestClient(app)
        resp = client.get("/admin/api/blueprints")
        assert resp.status_code == 200
        names = [b["display_name"] for b in resp.json()]
        assert "Family Accounting" in names


# ── auth required ─────────────────────────────────────────────────────────────

def test_endpoints_require_auth(db):
    """Without overriding require_auth, all endpoints should return 401."""
    app = FastAPI()
    app.include_router(api_router, prefix="/admin/api")

    with patch("app.admin.api.SessionLocal", return_value=_CM(db)):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/admin/api/groups").status_code == 401
        assert client.get("/admin/api/admins").status_code == 401
        assert client.get("/admin/api/blueprints").status_code == 401
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd orchestrator
python -m pytest tests/test_admin_api.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.admin.api'`

- [ ] **Step 3: Implement api.py**

Create `orchestrator/app/admin/api.py`:

```python
"""Admin panel REST API endpoints."""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.admin.auth import require_auth
from app.config import settings
from app.db.models import AdminNumbers, Blueprint, GroupRegistry
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter()


def _bridge_headers() -> dict:
    if settings.bridge_secret:
        return {"Authorization": f"Bearer {settings.bridge_secret}"}
    return {}


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginRequest):
    from app.admin.auth import AdminAuthError, create_token
    try:
        token = create_token(body.password)
        return {"token": token}
    except AdminAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


# ── Groups ────────────────────────────────────────────────────────────────────

class RegisterGroupRequest(BaseModel):
    group_jid: str
    blueprint_id: str


@router.get("/groups", dependencies=[Depends(require_auth)])
async def list_groups():
    # Fetch group names from bridge
    name_map: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.bridge_url}/groups",
                headers=_bridge_headers(),
            )
            if resp.status_code == 200:
                for g in resp.json().get("groups", []):
                    name_map[g["jid"]] = g["name"]
    except Exception:
        logger.warning("Could not fetch group names from bridge")

    with SessionLocal() as db:
        rows = db.query(GroupRegistry).all()
        blueprints = {b.id: b.display_name for b in db.query(Blueprint).all()}
        return [
            {
                "group_jid": r.group_jid,
                "group_name": name_map.get(r.group_jid, r.group_jid),
                "blueprint_id": r.blueprint_id,
                "blueprint_name": blueprints.get(r.blueprint_id, r.blueprint_id),
                "status": r.status,
            }
            for r in rows
        ]


@router.post("/groups", dependencies=[Depends(require_auth)])
def register_group(body: RegisterGroupRequest):
    with SessionLocal() as db:
        existing = db.get(GroupRegistry, body.group_jid)
        if existing:
            raise HTTPException(status_code=409, detail="Group already registered")
        bp = db.get(Blueprint, body.blueprint_id)
        if not bp:
            raise HTTPException(status_code=404, detail="Blueprint not found")
        db.add(GroupRegistry(group_jid=body.group_jid, blueprint_id=body.blueprint_id))
        db.commit()
    return {"ok": True}


@router.delete("/groups/{group_jid:path}", dependencies=[Depends(require_auth)])
def delete_group(group_jid: str):
    with SessionLocal() as db:
        row = db.get(GroupRegistry, group_jid)
        if not row:
            raise HTTPException(status_code=404, detail="Group not found")
        db.delete(row)
        db.commit()
    return {"ok": True}


@router.get("/bridge-groups", dependencies=[Depends(require_auth)])
async def bridge_groups():
    """Return groups the bot is in that are NOT yet registered."""
    with SessionLocal() as db:
        registered = {r.group_jid for r in db.query(GroupRegistry).all()}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.bridge_url}/groups",
                headers=_bridge_headers(),
            )
            resp.raise_for_status()
            all_groups = resp.json().get("groups", [])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bridge unreachable: {exc}")

    return [g for g in all_groups if g["jid"] not in registered]


# ── Admins ────────────────────────────────────────────────────────────────────

class AddAdminRequest(BaseModel):
    phone_number: str


@router.get("/admins", dependencies=[Depends(require_auth)])
def list_admins():
    with SessionLocal() as db:
        rows = db.query(AdminNumbers).all()
        return [{"phone_number": r.phone_number, "label": r.label} for r in rows]


@router.post("/admins", dependencies=[Depends(require_auth)])
def add_admin(body: AddAdminRequest):
    with SessionLocal() as db:
        if db.get(AdminNumbers, body.phone_number):
            raise HTTPException(status_code=409, detail="Admin already exists")
        db.add(AdminNumbers(phone_number=body.phone_number))
        db.commit()
    return {"ok": True}


@router.delete("/admins/{phone_number}", dependencies=[Depends(require_auth)])
def delete_admin(phone_number: str):
    with SessionLocal() as db:
        row = db.get(AdminNumbers, phone_number)
        if not row:
            raise HTTPException(status_code=404, detail="Admin not found")
        db.delete(row)
        db.commit()
    return {"ok": True}


# ── Blueprints ────────────────────────────────────────────────────────────────

@router.get("/blueprints", dependencies=[Depends(require_auth)])
def list_blueprints():
    with SessionLocal() as db:
        rows = db.query(Blueprint).all()
        return [
            {
                "id": b.id,
                "display_name": b.display_name,
                "tools_count": len(json.loads(b.tools_enabled or "[]")),
                "system_prompt_preview": b.system_prompt[:100] if b.system_prompt else "",
            }
            for b in rows
        ]
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd orchestrator
python -m pytest tests/test_admin_api.py -v
```
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/admin/api.py orchestrator/tests/test_admin_api.py
git commit -m "feat: admin panel API endpoints — groups, admins, blueprints"
```

---

## Task 4: Router and Static File Scaffold

**Files:**
- Create: `orchestrator/app/admin/router.py`
- Create: `orchestrator/app/static/admin/index.html` (placeholder)
- Create: `orchestrator/app/static/admin/app.js` (placeholder)
- Create: `orchestrator/app/static/admin/style.css` (placeholder)
- Modify: `orchestrator/app/main.py`

- [ ] **Step 1: Create router.py**

Create `orchestrator/app/admin/router.py`:

```python
"""Admin panel router — serves static SPA and mounts API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.staticfiles import StaticFiles

from app.admin.api import router as api_router
from app.config import settings

_STATIC_DIR = Path(__file__).parent.parent / "static" / "admin"

router = APIRouter()

# Include API sub-router
router.include_router(api_router, prefix="/api")


@router.get("/")
@router.get("")
def admin_root():
    if not settings.admin_ui_password:
        return Response(
            content="ADMIN_UI_PASSWORD is not set. Add it to .env and restart.",
            status_code=503,
            media_type="text/plain",
        )
    index = _STATIC_DIR / "index.html"
    return Response(content=index.read_bytes(), media_type="text/html")


def get_static_dir() -> Path:
    return _STATIC_DIR
```

- [ ] **Step 2: Create placeholder static files**

Create `orchestrator/app/static/admin/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>WhatsApp Agent Engine — Admin</title>
  <link rel="stylesheet" href="/admin/static/style.css">
</head>
<body>
  <div id="app">Loading...</div>
  <script src="/admin/static/app.js"></script>
</body>
</html>
```

Create `orchestrator/app/static/admin/style.css`:

```css
/* placeholder */
body { background: #0d1117; color: #e2e8f0; font-family: sans-serif; margin: 0; }
```

Create `orchestrator/app/static/admin/app.js`:

```js
// placeholder
document.getElementById('app').textContent = 'Admin panel loading...';
```

- [ ] **Step 3: Mount admin router in main.py**

In `orchestrator/app/main.py`, add import after the existing imports:

```python
from app.admin.router import router as admin_router, get_static_dir
from fastapi.staticfiles import StaticFiles
```

Add after `app = FastAPI(...)`:

```python
app.include_router(admin_router, prefix="/admin")
app.mount("/admin/static", StaticFiles(directory=str(get_static_dir())), name="admin_static")
```

- [ ] **Step 4: Run full test suite to confirm nothing broke**

```bash
cd orchestrator
python -m pytest --tb=short -q
```
Expected: all existing tests pass

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/admin/router.py orchestrator/app/static/ orchestrator/app/main.py
git commit -m "feat: mount admin router and static file scaffold"
```

---

## Task 5: Frontend

**Files:**
- Modify: `orchestrator/app/static/admin/index.html`
- Modify: `orchestrator/app/static/admin/app.js`
- Modify: `orchestrator/app/static/admin/style.css`

- [ ] **Step 1: Write style.css**

Replace `orchestrator/app/static/admin/style.css` with:

```css
*, *::before, *::after { box-sizing: border-box; }

:root {
  --bg: #0d1117;
  --surface: #161b22;
  --surface2: #1e2433;
  --border: #2d3a52;
  --text: #e2e8f0;
  --muted: #7c8db0;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --danger: #ef4444;
  --sidebar-w: 200px;
}

body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }

/* Login */
.login-wrap { display: flex; align-items: center; justify-content: center; min-height: 100vh; }
.login-box { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 32px; width: 340px; }
.login-box h1 { margin: 0 0 8px; font-size: 18px; }
.login-box p { margin: 0 0 24px; color: var(--muted); font-size: 13px; }
.login-box .error { color: var(--danger); font-size: 12px; margin-top: 8px; }

/* Layout */
.layout { display: flex; min-height: 100vh; }
.sidebar { width: var(--sidebar-w); background: var(--surface); border-right: 1px solid var(--border); padding: 20px 12px; display: flex; flex-direction: column; gap: 4px; flex-shrink: 0; }
.sidebar-title { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; padding: 8px 10px 4px; }
.nav-item { padding: 9px 12px; border-radius: 6px; cursor: pointer; color: var(--muted); display: flex; align-items: center; gap: 8px; transition: background 0.15s; }
.nav-item:hover { background: var(--surface2); color: var(--text); }
.nav-item.active { background: var(--surface2); color: var(--text); font-weight: 500; }
.main { flex: 1; padding: 32px; overflow-y: auto; }

/* Page */
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
.page-header h2 { margin: 0; font-size: 18px; }

/* Table */
.table { width: 100%; border-collapse: collapse; }
.table th { text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); border-bottom: 1px solid var(--border); }
.table td { padding: 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.table tr:last-child td { border-bottom: none; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; background: var(--surface2); color: var(--muted); }

/* Buttons */
.btn { padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; font-weight: 500; transition: background 0.15s; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-danger { background: transparent; color: var(--danger); border: 1px solid var(--danger); padding: 5px 10px; font-size: 12px; }
.btn-danger:hover { background: var(--danger); color: #fff; }

/* Forms */
.form-group { margin-bottom: 16px; }
.form-group label { display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); margin-bottom: 6px; }
.form-group input, .form-group select { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 8px 10px; border-radius: 6px; font-size: 13px; }
.form-group input:focus, .form-group select:focus { outline: none; border-color: var(--accent); }

/* Modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 24px; width: 420px; }
.modal h3 { margin: 0 0 4px; }
.modal .subtitle { color: var(--muted); font-size: 12px; margin: 0 0 20px; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }

/* Inline add row */
.add-row { display: flex; gap: 8px; margin-top: 16px; }
.add-row input { flex: 1; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 8px 10px; border-radius: 6px; font-size: 13px; }

/* Blueprint preview */
.bp-prompt { font-size: 12px; color: var(--muted); font-family: monospace; }

/* Empty state */
.empty { color: var(--muted); text-align: center; padding: 40px; font-size: 13px; }
```

- [ ] **Step 2: Write app.js**

Replace `orchestrator/app/static/admin/app.js` with:

```js
// WhatsApp Agent Engine — Admin Panel
// Vanilla JS SPA with hash-based routing

const API = '/admin/api';

// ── Auth ──────────────────────────────────────────────────────────────────────

function getToken() { return localStorage.getItem('admin_token'); }
function setToken(t) { localStorage.setItem('admin_token', t); }
function clearToken() { localStorage.removeItem('admin_token'); }

async function apiFetch(path, opts = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401) { clearToken(); route(); return null; }
  return res;
}

// ── Router ────────────────────────────────────────────────────────────────────

async function route() {
  const app = document.getElementById('app');
  const hash = location.hash.replace('#', '') || 'groups';
  if (!getToken()) { renderLogin(app); return; }
  if (hash === 'groups') await renderGroups(app);
  else if (hash === 'admins') await renderAdmins(app);
  else if (hash === 'blueprints') await renderBlueprints(app);
  else await renderGroups(app);
}

window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);

// ── Layout shell ──────────────────────────────────────────────────────────────

function layout(page, content) {
  const nav = [
    { hash: 'groups',     icon: '🏠', label: 'Groups' },
    { hash: 'admins',     icon: '👥', label: 'Admins' },
    { hash: 'blueprints', icon: '📋', label: 'Blueprints' },
  ];
  return `
    <div class="layout">
      <nav class="sidebar">
        <div class="sidebar-title">Admin Panel</div>
        ${nav.map(n => `
          <div class="nav-item ${page === n.hash ? 'active' : ''}" onclick="location.hash='${n.hash}'">
            ${n.icon} ${n.label}
          </div>`).join('')}
        <div style="flex:1"></div>
        <div class="nav-item" onclick="clearToken();route()">🚪 Sign out</div>
      </nav>
      <main class="main">${content}</main>
    </div>`;
}

// ── Login ─────────────────────────────────────────────────────────────────────

function renderLogin(app) {
  app.innerHTML = `
    <div class="login-wrap">
      <div class="login-box">
        <h1>Admin Panel</h1>
        <p>WhatsApp Agent Engine</p>
        <div class="form-group">
          <label>Password</label>
          <input id="pw" type="password" placeholder="Enter admin password" onkeydown="if(event.key==='Enter')doLogin()">
        </div>
        <button class="btn btn-primary" style="width:100%" onclick="doLogin()">Sign in</button>
        <div id="login-err" class="error"></div>
      </div>
    </div>`;
  document.getElementById('pw').focus();
}

async function doLogin() {
  const pw = document.getElementById('pw').value;
  const res = await fetch(API + '/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: pw }),
  });
  if (res.ok) {
    const { token } = await res.json();
    setToken(token);
    location.hash = 'groups';
    route();
  } else {
    document.getElementById('login-err').textContent = 'Incorrect password.';
  }
}

// ── Groups ────────────────────────────────────────────────────────────────────

async function renderGroups(app) {
  app.innerHTML = layout('groups', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/groups');
  if (!res) return;
  const groups = await res.json();

  const rows = groups.length
    ? groups.map(g => `
        <tr>
          <td>${escHtml(g.group_name)}<br><span style="font-size:11px;color:var(--muted)">${escHtml(g.group_jid)}</span></td>
          <td><span class="badge">${escHtml(g.blueprint_name)}</span></td>
          <td><span class="badge">${escHtml(g.status)}</span></td>
          <td><button class="btn btn-danger" onclick="deleteGroup('${escAttr(g.group_jid)}')">Remove</button></td>
        </tr>`).join('')
    : '<tr><td colspan="4" class="empty">No groups registered yet.</td></tr>';

  app.innerHTML = layout('groups', `
    <div class="page-header">
      <h2>Groups</h2>
      <button class="btn btn-primary" onclick="openRegisterModal()">+ Register Group</button>
    </div>
    <table class="table">
      <thead><tr><th>Group</th><th>Blueprint</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div id="modal-container"></div>`);
}

async function deleteGroup(jid) {
  if (!confirm(`Remove group ${jid}?`)) return;
  await apiFetch('/groups/' + encodeURIComponent(jid), { method: 'DELETE' });
  renderGroups(document.getElementById('app'));
}

async function openRegisterModal() {
  const [bpRes, grpRes] = await Promise.all([
    apiFetch('/blueprints'),
    apiFetch('/bridge-groups'),
  ]);
  if (!bpRes || !grpRes) return;
  const blueprints = await bpRes.json();
  const bridgeGroups = await grpRes.json();

  const groupOpts = bridgeGroups.length
    ? bridgeGroups.map(g => `<option value="${escAttr(g.jid)}">${escHtml(g.name)}</option>`).join('')
    : '<option value="" disabled>No unregistered groups found</option>';

  const bpOpts = blueprints.map(b =>
    `<option value="${escAttr(b.id)}">${escHtml(b.display_name)}</option>`).join('');

  document.getElementById('modal-container').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closeModal()">
      <div class="modal">
        <h3>Register Group</h3>
        <p class="subtitle">Select a group the bot is in and assign a blueprint</p>
        <div class="form-group">
          <label>Group</label>
          <select id="modal-group">${groupOpts}</select>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">Only unregistered groups shown</div>
        </div>
        <div class="form-group">
          <label>Blueprint</label>
          <select id="modal-bp">${bpOpts}</select>
        </div>
        <div class="modal-footer">
          <button class="btn" style="background:transparent;color:var(--muted)" onclick="closeModal()">Cancel</button>
          <button class="btn btn-primary" onclick="submitRegisterGroup()">Register</button>
        </div>
      </div>
    </div>`;
}

function closeModal() {
  const c = document.getElementById('modal-container');
  if (c) c.innerHTML = '';
}

async function submitRegisterGroup() {
  const jid = document.getElementById('modal-group').value;
  const bp  = document.getElementById('modal-bp').value;
  if (!jid) return;
  await apiFetch('/groups', { method: 'POST', body: JSON.stringify({ group_jid: jid, blueprint_id: bp }) });
  closeModal();
  renderGroups(document.getElementById('app'));
}

// ── Admins ────────────────────────────────────────────────────────────────────

async function renderAdmins(app) {
  app.innerHTML = layout('admins', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/admins');
  if (!res) return;
  const admins = await res.json();

  const rows = admins.length
    ? admins.map(a => `
        <tr>
          <td>${escHtml(a.phone_number)}</td>
          <td>${escHtml(a.label || '—')}</td>
          <td><button class="btn btn-danger" onclick="deleteAdmin('${escAttr(a.phone_number)}')">Remove</button></td>
        </tr>`).join('')
    : '<tr><td colspan="3" class="empty">No admins configured.</td></tr>';

  app.innerHTML = layout('admins', `
    <div class="page-header"><h2>Admins</h2></div>
    <table class="table">
      <thead><tr><th>Phone Number</th><th>Label</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="add-row">
      <input id="new-phone" type="text" placeholder="e.g. 972501234567" onkeydown="if(event.key==='Enter')addAdmin()">
      <button class="btn btn-primary" onclick="addAdmin()">+ Add Admin</button>
    </div>`);
}

async function addAdmin() {
  const phone = document.getElementById('new-phone').value.trim();
  if (!phone) return;
  await apiFetch('/admins', { method: 'POST', body: JSON.stringify({ phone_number: phone }) });
  renderAdmins(document.getElementById('app'));
}

async function deleteAdmin(phone) {
  if (!confirm(`Remove admin ${phone}?`)) return;
  await apiFetch('/admins/' + encodeURIComponent(phone), { method: 'DELETE' });
  renderAdmins(document.getElementById('app'));
}

// ── Blueprints ────────────────────────────────────────────────────────────────

async function renderBlueprints(app) {
  app.innerHTML = layout('blueprints', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/blueprints');
  if (!res) return;
  const blueprints = await res.json();

  const rows = blueprints.map(b => `
    <tr>
      <td><strong>${escHtml(b.display_name)}</strong><br><span style="font-size:11px;color:var(--muted)">${escHtml(b.id)}</span></td>
      <td><span class="badge">${b.tools_count} tools</span></td>
      <td class="bp-prompt">${escHtml(b.system_prompt_preview)}…</td>
    </tr>`).join('');

  app.innerHTML = layout('blueprints', `
    <div class="page-header"><h2>Blueprints</h2></div>
    <table class="table">
      <thead><tr><th>Blueprint</th><th>Tools</th><th>System Prompt</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`);
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) { return escHtml(s); }
```

- [ ] **Step 3: Write index.html**

Replace `orchestrator/app/static/admin/index.html` with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>WhatsApp Agent Engine — Admin</title>
  <link rel="stylesheet" href="/admin/static/style.css">
</head>
<body>
  <div id="app"></div>
  <script src="/admin/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator/app/static/admin/
git commit -m "feat: admin panel frontend — login, groups, admins, blueprints"
```

---

## Task 6: Wire Up, Configure, and Deploy

**Files:**
- Modify: `.env`
- Modify: `orchestrator/app/main.py` (verify mount order)

- [ ] **Step 1: Add ADMIN_UI_PASSWORD to .env**

Open `.env` and add:
```
ADMIN_UI_PASSWORD=<choose a strong password>
```

- [ ] **Step 2: Verify static mount order in main.py**

The `app.mount("/admin/static", ...)` line must come **after** `app.include_router(admin_router, prefix="/admin")`. FastAPI routes are matched in registration order. Verify the order is:

```python
app.include_router(admin_router, prefix="/admin")
app.mount("/admin/static", StaticFiles(directory=str(get_static_dir())), name="admin_static")
```

- [ ] **Step 3: Run full test suite**

```bash
cd orchestrator
python -m pytest --tb=short -q
```
Expected: all tests pass

- [ ] **Step 4: Smoke test locally**

```bash
cd orchestrator
ADMIN_UI_PASSWORD=test123 uvicorn app.main:app --port 8001
```

Open `http://localhost:8001/admin` — should show login page. Login with `test123`, navigate Groups/Admins/Blueprints.

- [ ] **Step 5: Final commit and push**

```bash
git add .env orchestrator/app/main.py
git commit -m "feat: admin panel complete — groups, admins, blueprints, auth"
git push origin feat/accounting-enhancements
```

- [ ] **Step 6: Deploy to Hetzner**

```bash
ssh -i ~/.ssh/hetzner_ta125 root@178.105.63.248 "cd /opt/whatsapp-agent && git pull origin feat/accounting-enhancements && docker compose build orchestrator && docker compose up -d orchestrator"
```

Open `http://178.105.63.248:8000/admin` — note: port 8000 is only accessible if exposed. If the orchestrator port isn't exposed, either:
- Add `ports: ["8080:8000"]` to the orchestrator service in `docker-compose.yml`, or
- Access via `ssh -L 8001:localhost:8000 root@178.105.63.248` tunnel then open `http://localhost:8001/admin`
