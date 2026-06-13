"""Admin panel REST API endpoints."""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.admin.auth import require_auth
from app.config import settings
from app.db.models import AdminNumbers, Blueprint, EmailAllowlist, GroupParticipant, GroupRegistry, SystemConfig, UserAccount, UserProfile
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


async def _fetch_bridge_groups() -> dict[str, dict]:
    """Fetch {jid: {name, participants}} map from bridge. Returns empty dict on failure."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.bridge_url}/groups",
                headers=_bridge_headers(),
            )
            if resp.status_code == 200:
                return {
                    g["jid"]: {
                        "name": g.get("name", g["jid"]),
                        "participants": g.get("participants", []),
                    }
                    for g in resp.json().get("groups", [])
                }
    except Exception:
        logger.warning("Could not fetch groups from bridge", exc_info=True)
    return {}


@router.get("/groups", dependencies=[Depends(require_auth)])
async def list_groups():
    bridge_map = await _fetch_bridge_groups()
    bot_phone = settings.bot_phone_number or ""

    with SessionLocal() as db:
        rows = db.query(GroupRegistry).all()
        blueprints = {b.id: b.display_name for b in db.query(Blueprint).all()}

        # Pre-load all GroupParticipant rows for name lookups.
        # gp_by_group: per-group phone→GP (for in-group lookup)
        # gp_by_phone: global phone→GP fallback for participants who are known
        #   in *some* group but haven't messaged the bot in *this* group yet.
        all_gp = db.query(GroupParticipant).all()
        gp_by_group: dict[str, dict[str, GroupParticipant]] = {}
        gp_by_phone: dict[str, GroupParticipant] = {}
        for gp in all_gp:
            gp_by_group.setdefault(gp.group_jid, {})[gp.phone] = gp
            # Keep first occurrence (prefer active rows; order is stable enough)
            if gp.phone not in gp_by_phone:
                gp_by_phone[gp.phone] = gp

        # Build name→phone map from AdminNumbers for reverse lookup.
        # Keyed by lowercase first-word of the label (e.g. "eran", "sivan").
        # This lets us show actual phone numbers for admins whose DB entry
        # uses a WhatsApp LID instead of a real phone.
        admin_label_to_phone: dict[str, str] = {}
        for an in db.query(AdminNumbers).all():
            if an.label:
                key = an.label.strip().split()[0].lower()
                admin_label_to_phone[key] = an.phone_number

        result = []
        for r in rows:
            bridge_info = bridge_map.get(r.group_jid, {})
            group_name = bridge_info.get("name") or r.group_jid
            bridge_participants = bridge_info.get("participants", [])
            db_phones = gp_by_group.get(r.group_jid, {})

            if bridge_participants:
                # Use bridge as the source of truth for membership.
                # Bot is already excluded by the bridge (/groups filters sock.user.id).
                members = []
                for p in bridge_participants:
                    phone = p["jid"].split("@")[0].split(":")[0]
                    if bot_phone and phone == bot_phone:
                        continue  # belt-and-suspenders fallback
                    # Look up in this group first; fall back to any group the
                    # person has ever appeared in (covers solo groups where the
                    # member has never sent a message to the bot).
                    gp = db_phones.get(phone) or gp_by_phone.get(phone)
                    if gp:
                        name = gp.admin_name or gp.push_name
                        # Try to resolve the actual phone from AdminNumbers
                        first = (name or "").split()[0].lower()
                        display_phone = admin_label_to_phone.get(first) or phone
                    else:
                        name = None
                        display_phone = phone
                    members.append({"phone": display_phone, "name": name})
            else:
                # Bridge data unavailable — fall back to DB participants
                members = []
                for gp in db_phones.values():
                    if gp.status != "active" or gp.phone == bot_phone:
                        continue
                    name = gp.admin_name or gp.push_name
                    first = (name or "").split()[0].lower()
                    display_phone = admin_label_to_phone.get(first) or gp.phone
                    members.append({"phone": display_phone, "name": name})

            result.append({
                "group_jid": r.group_jid,
                "group_name": group_name,
                "blueprint_id": r.blueprint_id,
                "blueprint_name": blueprints.get(r.blueprint_id, r.blueprint_id),
                "status": r.status,
                "member_count": len(members),
                "members": members,
            })
        return result


@router.post("/groups", dependencies=[Depends(require_auth)])
def register_group(body: RegisterGroupRequest):
    from app import main as _main
    with SessionLocal() as db:
        existing = db.get(GroupRegistry, body.group_jid)
        if existing:
            raise HTTPException(status_code=409, detail="Group already registered")
        bp = db.get(Blueprint, body.blueprint_id)
        if not bp:
            raise HTTPException(status_code=404, detail="Blueprint not found")
        db.add(GroupRegistry(group_jid=body.group_jid, blueprint_id=body.blueprint_id))
        db.commit()
    _main.router.invalidate(body.group_jid)
    return {"ok": True}


@router.delete("/groups/{group_jid:path}", dependencies=[Depends(require_auth)])
def delete_group(group_jid: str):
    from app.db.models import AutomationRule, UserAccount
    from app import main as _main
    with SessionLocal() as db:
        row = db.get(GroupRegistry, group_jid)
        if not row:
            raise HTTPException(status_code=404, detail="Group not found")
        # Remove child rows that have FK constraints on group_registry.group_jid
        # (PRAGMA foreign_keys=ON is active so we must clear them first)
        db.query(GroupParticipant).filter_by(group_jid=group_jid).delete()
        db.query(AutomationRule).filter_by(group_jid=group_jid).delete()
        db.query(UserAccount).filter_by(group_jid=group_jid).delete()
        db.delete(row)
        db.commit()
    _main.router.invalidate(group_jid)
    return {"ok": True}


@router.get("/bridge-groups", dependencies=[Depends(require_auth)])
async def bridge_groups():
    """Return groups the bot is in that are NOT yet registered."""
    with SessionLocal() as db:
        registered = {r.group_jid for r in db.query(GroupRegistry).all()}

    bridge_map = await _fetch_bridge_groups()
    if not bridge_map:
        raise HTTPException(status_code=502, detail="Bridge unreachable or returned no groups")

    return [
        {"jid": jid, "name": info["name"]}
        for jid, info in bridge_map.items()
        if jid not in registered
    ]


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
    for name, entry in reg.list_tools().items():
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

    # Refresh the in-process cache so the change takes effect immediately
    from app.agent_runner import AgentRunner
    AgentRunner.refresh_disabled_tools()

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


# -- People ------------------------------------------------------------------

@router.get("/people")
def list_people(_=Depends(require_auth)):
    """Unified list: everyone from admin_numbers + user_accounts."""
    with SessionLocal() as db:
        # Collect all unique phones
        admin_rows = {r.phone_number: r for r in db.query(AdminNumbers).all()}
        account_rows = db.query(UserAccount).all()
        account_by_phone: dict[str, list] = {}
        for acct in account_rows:
            account_by_phone.setdefault(acct.phone, []).append(acct)

        all_phones = set(admin_rows) | set(account_by_phone)
        result = []
        for phone in sorted(all_phones):
            profile = db.query(UserProfile).filter_by(phone=phone).first()
            admin = admin_rows.get(phone)
            # Prefer owner account, fall back to first account
            accounts = account_by_phone.get(phone, [])
            owner_acct = next((a for a in accounts if a.role == "owner"), accounts[0] if accounts else None)
            grp = db.query(GroupRegistry).filter_by(group_jid=owner_acct.group_jid).first() if owner_acct else None
            result.append({
                "phone": phone,
                "display_name": profile.display_name if profile else None,
                "email": profile.email if profile else None,
                "is_admin": admin is not None,
                "admin_label": admin.label if admin else None,
                "group_jid": owner_acct.group_jid if owner_acct else None,
                "role": owner_acct.role if owner_acct else None,
                "group_type": grp.group_type if grp else None,
                "created_at": owner_acct.created_at.isoformat() if owner_acct and owner_acct.created_at else None,
            })
        return result


class AddPersonRequest(BaseModel):
    phone: str
    display_name: str | None = None
    group_jid: str | None = None
    role: str = "owner"
    is_admin: bool = False
    admin_label: str | None = None


@router.post("/people")
def add_person(body: AddPersonRequest, _=Depends(require_auth)):
    with SessionLocal() as db:
        # Display name
        if body.display_name:
            profile = db.query(UserProfile).filter_by(phone=body.phone).first()
            if profile is None:
                db.add(UserProfile(phone=body.phone, display_name=body.display_name))
            else:
                profile.display_name = body.display_name

        # User account
        if body.group_jid:
            existing = db.query(UserAccount).filter_by(phone=body.phone, group_jid=body.group_jid).first()
            if not existing:
                db.add(UserAccount(phone=body.phone, group_jid=body.group_jid, role=body.role))

        # Admin
        if body.is_admin:
            if not db.query(AdminNumbers).filter_by(phone_number=body.phone).first():
                db.add(AdminNumbers(phone_number=body.phone, label=body.admin_label))

        db.commit()
        return {"ok": True}


class UpdatePersonRequest(BaseModel):
    display_name: str


@router.put("/people/{phone}")
def update_person(phone: str, body: UpdatePersonRequest, _=Depends(require_auth)):
    with SessionLocal() as db:
        profile = db.query(UserProfile).filter_by(phone=phone).first()
        if profile is None:
            db.add(UserProfile(phone=phone, display_name=body.display_name))
        else:
            profile.display_name = body.display_name
        db.commit()
        return {"phone": phone, "display_name": body.display_name}


class ToggleAdminRequest(BaseModel):
    is_admin: bool
    label: str | None = None


@router.put("/people/{phone}/admin")
def toggle_admin(phone: str, body: ToggleAdminRequest, _=Depends(require_auth)):
    with SessionLocal() as db:
        existing = db.query(AdminNumbers).filter_by(phone_number=phone).first()
        if body.is_admin:
            if not existing:
                db.add(AdminNumbers(phone_number=phone, label=body.label))
        else:
            if existing:
                db.delete(existing)
        db.commit()
        return {"phone": phone, "is_admin": body.is_admin}


@router.delete("/people/{phone}")
def delete_person(phone: str, _=Depends(require_auth)):
    """Remove user account (does not remove admin status)."""
    with SessionLocal() as db:
        db.query(UserAccount).filter_by(phone=phone).delete()
        db.commit()
        return {"deleted": phone}


# -- Pending registrations ---------------------------------------------------

@router.get("/people/pending")
def list_pending(_=Depends(require_auth)):
    """Groups registered as 'unregistered' — awaiting approval."""
    with SessionLocal() as db:
        pending_groups = db.query(GroupRegistry).filter_by(group_type="unregistered").all()
        result = []
        for grp in pending_groups:
            participants = db.query(GroupParticipant).filter_by(
                group_jid=grp.group_jid, status="active"
            ).all()
            result.append({
                "group_jid": grp.group_jid,
                "human_phones": [p.phone for p in participants],
                "candidate_type": "personal" if len(participants) == 1 else "shared",
            })
        return result


class ApproveRegistrationRequest(BaseModel):
    group_type: str = "personal"  # personal | shared


@router.post("/people/pending/{group_jid}/approve")
async def approve_registration(group_jid: str, body: ApproveRegistrationRequest, _=Depends(require_auth)):
    from app import bridge_client as _bc
    with SessionLocal() as db:
        grp = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
        if not grp:
            raise HTTPException(status_code=404, detail="Group not found")
        grp.group_type = body.group_type
        participants = db.query(GroupParticipant).filter_by(
            group_jid=group_jid, status="active"
        ).all()
        phones = [p.phone for p in participants]
        role = "owner" if len(phones) == 1 else "member"
        for phone in phones:
            existing = db.query(UserAccount).filter_by(phone=phone, group_jid=group_jid).first()
            if not existing:
                db.add(UserAccount(phone=phone, group_jid=group_jid, role=role))
        db.commit()
    try:
        await _bc.send_message(group_jid, "Your account is ready. You can start recording transactions here.")
    except Exception:
        pass
    return {"ok": True, "group_jid": group_jid, "group_type": body.group_type}


@router.post("/people/pending/{group_jid}/reject")
async def reject_registration(group_jid: str, _=Depends(require_auth)):
    from app import bridge_client as _bc
    with SessionLocal() as db:
        db.query(GroupRegistry).filter_by(group_jid=group_jid).delete()
        db.commit()
    try:
        await _bc.send_message(group_jid, "This group was not approved.")
    except Exception:
        pass
    return {"ok": True}


# -- Expanded person update --------------------------------------------------

class UpdatePersonFullRequest(BaseModel):
    display_name: str | None = None
    email: str | None = None
    is_admin: bool | None = None
    admin_label: str | None = None


@router.patch("/people/{phone}")
def patch_person(phone: str, body: UpdatePersonFullRequest, _=Depends(require_auth)):
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

                # Remove old email from allowlist when email changes or is cleared.
                # Note: `new_email != old_email_norm` is True even when new_email="",
                # so the cleared case is handled here (no separate else branch needed).
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

        if body.is_admin is not None:
            existing_admin = db.query(AdminNumbers).filter_by(phone_number=phone).first()
            if body.is_admin and not existing_admin:
                db.add(AdminNumbers(phone_number=phone, label=body.admin_label))
            elif not body.is_admin and existing_admin:
                db.delete(existing_admin)
            elif body.is_admin and existing_admin and body.admin_label is not None:
                existing_admin.label = body.admin_label

        db.commit()
        return {"ok": True}


# -- Settings ----------------------------------------------------------------

_EDITABLE_SETTINGS = {
    "cross_group_confirmation_timeout_hours",
    "group_registration_timeout_hours",
}


@router.get("/settings")
def get_settings(_=Depends(require_auth)):
    with SessionLocal() as db:
        rows = db.query(SystemConfig).filter(
            SystemConfig.key.in_(_EDITABLE_SETTINGS)
        ).all()
        result = {r.key: r.value for r in rows}
        defaults = {
            "cross_group_confirmation_timeout_hours": "24",
            "group_registration_timeout_hours": "24",
        }
        for k, v in defaults.items():
            result.setdefault(k, v)
        return result


class UpdateSettingRequest(BaseModel):
    value: str


@router.put("/settings/{key}")
def update_setting(key: str, body: UpdateSettingRequest, _=Depends(require_auth)):
    if key not in _EDITABLE_SETTINGS:
        raise HTTPException(status_code=400, detail=f"Unknown setting: {key}")
    with SessionLocal() as db:
        row = db.query(SystemConfig).filter_by(key=key).first()
        if row is None:
            row = SystemConfig(key=key, value=body.value)
            db.add(row)
        else:
            row.value = body.value
        db.commit()
        return {"key": key, "value": body.value}


# -- Email Allowlist ---------------------------------------------------------

class AddAllowlistRequest(BaseModel):
    email: str
    display_name: str | None = None


@router.get("/settings/email-allowlist", dependencies=[Depends(require_auth)])
def list_email_allowlist():
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
    email = body.email.strip().lower()
    with SessionLocal() as db:
        if db.get(EmailAllowlist, email):
            raise HTTPException(status_code=409, detail="Email already in allowlist")
        db.add(EmailAllowlist(email=email, display_name=body.display_name or None))
        db.commit()
    return {"ok": True}


@router.delete("/settings/email-allowlist/{email:path}", dependencies=[Depends(require_auth)])
def delete_email_allowlist(email: str):
    with SessionLocal() as db:
        row = db.get(EmailAllowlist, email.strip().lower())
        if not row:
            raise HTTPException(status_code=404, detail="Email not in allowlist")
        db.delete(row)
        db.commit()
    return {"ok": True}


# -- Request logs ------------------------------------------------------------

@router.get("/logs")
def get_logs(limit: int = 100, group_jid: str | None = None, _=Depends(require_auth)):
    from app.db.models import RequestLog
    with SessionLocal() as db:
        q = db.query(RequestLog).order_by(RequestLog.created_at.desc())
        if group_jid:
            q = q.filter(RequestLog.group_jid == group_jid)
        rows = q.limit(min(limit, 500)).all()
        return [
            {
                "id": r.id,
                "group_jid": r.group_jid,
                "blueprint_id": r.blueprint_id,
                "sender_phone": r.sender_phone,
                "history_pairs": r.history_pairs,
                "tool_count": r.tool_count,
                "stop_reason": r.stop_reason,
                "tool_calls_made": json.loads(r.tool_calls_made) if r.tool_calls_made else [],
                "error": r.error,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
