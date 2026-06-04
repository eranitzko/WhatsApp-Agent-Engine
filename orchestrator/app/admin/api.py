"""Admin panel REST API endpoints."""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.admin.auth import require_auth
from app.config import settings
from app.db.models import AdminNumbers, Blueprint, GroupRegistry, SystemConfig
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter()


def _bridge_headers() -> dict:
    if settings.bridge_secret:
        return {"Authorization": f"Bearer {settings.bridge_secret}"}
    return {}


# -- Login -------------------------------------------------------------------

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


# -- Groups ------------------------------------------------------------------

class RegisterGroupRequest(BaseModel):
    group_jid: str
    blueprint_id: str


async def _fetch_bridge_name_map() -> dict[str, str]:
    """Fetch {jid: name} map from bridge. Returns empty dict on failure."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.bridge_url}/groups",
                headers=_bridge_headers(),
            )
            if resp.status_code == 200:
                return {g["jid"]: g["name"] for g in resp.json().get("groups", [])}
    except Exception:
        logger.warning("Could not fetch group names from bridge", exc_info=True)
    return {}


@router.get("/groups", dependencies=[Depends(require_auth)])
async def list_groups():
    name_map = await _fetch_bridge_name_map()
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

    return [{"jid": g["jid"], "name": g.get("name", g["jid"])} for g in all_groups if g["jid"] not in registered]


# -- Admins ------------------------------------------------------------------

class AddAdminRequest(BaseModel):
    phone_number: str
    label: str | None = None


class UpdateAdminRequest(BaseModel):
    label: str | None = None


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
        db.add(AdminNumbers(phone_number=body.phone_number, label=body.label or None))
        db.commit()
    return {"ok": True}


@router.patch("/admins/{phone_number}", dependencies=[Depends(require_auth)])
def update_admin(phone_number: str, body: UpdateAdminRequest):
    with SessionLocal() as db:
        row = db.get(AdminNumbers, phone_number)
        if not row:
            raise HTTPException(status_code=404, detail="Admin not found")
        row.label = body.label or None
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


# -- Blueprints --------------------------------------------------------------

@router.get("/blueprints", dependencies=[Depends(require_auth)])
def list_blueprints():
    with SessionLocal() as db:
        rows = db.query(Blueprint).all()
        result = []
        for b in rows:
            try:
                tools_count = len(json.loads(b.tools_enabled or "[]"))
            except json.JSONDecodeError:
                logger.warning("Malformed tools_enabled for blueprint %s", b.id)
                tools_count = 0
            result.append({
                "id": b.id,
                "display_name": b.display_name,
                "tools_count": tools_count,
                "tools_list": b.tools_enabled or "[]",
                "system_prompt": b.system_prompt or "",
                "system_prompt_preview": b.system_prompt[:100] if b.system_prompt else "",
            })
        return result


# -- Tools -------------------------------------------------------------------

@router.get("/tools", dependencies=[Depends(require_auth)])
def list_tools():
    """Return all tools registered in the live ToolRegistry."""
    import json as _json
    from app import registry_ref as _ref

    try:
        reg = _ref.get_registry()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Tool registry not yet initialised")

    with SessionLocal() as db:
        disabled_row = db.get(SystemConfig, "disabled_tools")
        disabled = set(_json.loads(disabled_row.value)) if disabled_row and disabled_row.value else set()

        tool_to_bps: dict[str, list[str]] = {}
        for bp in db.query(Blueprint).all():
            try:
                for t in _json.loads(bp.tools_enabled or "[]"):
                    tool_to_bps.setdefault(t, []).append(bp.id)
            except _json.JSONDecodeError:
                pass

    result = []
    for name, entry in reg._tools.items():
        schema = entry["schema"]
        result.append({
            "name": name,
            "description": schema.get("description", ""),
            "category": schema.get("category", "other"),
            "blueprints_using": tool_to_bps.get(name, []),
            "globally_enabled": name not in disabled,
        })
    return sorted(result, key=lambda x: x["name"])


class UpdateBlueprintToolsRequest(BaseModel):
    tools_enabled: list[str]


@router.patch("/blueprints/{blueprint_id}/tools", dependencies=[Depends(require_auth)])
def update_blueprint_tools(blueprint_id: str, body: UpdateBlueprintToolsRequest):
    """Update tools_enabled for a blueprint. Validates all names against live registry."""
    import json as _json
    from app import registry_ref as _ref

    try:
        reg = _ref.get_registry()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Tool registry not yet initialised")

    unknown = [t for t in body.tools_enabled if not reg.has_tool(t)]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tools: {unknown}")

    with SessionLocal() as db:
        bp = db.get(Blueprint, blueprint_id)
        if not bp:
            raise HTTPException(status_code=404, detail="Blueprint not found")
        bp.tools_enabled = _json.dumps(body.tools_enabled)
        db.commit()
    return {"ok": True}


class UpdateToolEnabledRequest(BaseModel):
    enabled: bool


@router.patch("/tools/{tool_name}/enabled", dependencies=[Depends(require_auth)])
def update_tool_enabled(tool_name: str, body: UpdateToolEnabledRequest):
    """Globally enable or disable a tool via SystemConfig['disabled_tools']."""
    import json as _json
    from app import registry_ref as _ref

    try:
        reg = _ref.get_registry()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Tool registry not yet initialised")

    if not reg.has_tool(tool_name):
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not in registry")

    with SessionLocal() as db:
        row = db.get(SystemConfig, "disabled_tools")
        disabled = set(_json.loads(row.value)) if row and row.value else set()

        if body.enabled:
            disabled.discard(tool_name)
        else:
            disabled.add(tool_name)

        new_value = _json.dumps(sorted(disabled))
        if row:
            row.value = new_value
        else:
            db.add(SystemConfig(key="disabled_tools", value=new_value))
        db.commit()
    return {"ok": True}


@router.delete("/tools/{tool_name}/blueprints", dependencies=[Depends(require_auth)])
def remove_tool_from_blueprints(tool_name: str):
    """Remove a tool from every blueprint's tools_enabled list."""
    import json as _json
    updated: list[str] = []
    with SessionLocal() as db:
        for bp in db.query(Blueprint).all():
            try:
                tools = _json.loads(bp.tools_enabled or "[]")
            except _json.JSONDecodeError:
                continue
            if tool_name in tools:
                bp.tools_enabled = _json.dumps([t for t in tools if t != tool_name])
                updated.append(bp.id)
        db.commit()
    return {"ok": True, "blueprints_updated": updated}
