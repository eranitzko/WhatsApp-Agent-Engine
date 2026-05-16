import httpx
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry, AdminNumbers, ConversationHistory

_VALID_TRIGGERS = {"always", "mention", "prefix"}


class CommandHandler:
    COMMANDS = {"/bind", "/unbind", "/pause", "/resume", "/blueprints", "/sync"}

    def __init__(self, bridge_url: str = ""):
        self._bridge_url = bridge_url

    def is_command(self, text: str) -> bool:
        if not text:
            return False
        first_word = text.strip().split()[0].lower()
        return first_word in self.COMMANDS

    async def handle(self, db: Session, group_jid: str, sender_phone: str, text: str) -> str | None:
        if not self._is_admin(db, sender_phone):
            return None

        parts = text.strip().split()
        cmd = parts[0].lower()

        if cmd == "/blueprints":
            blueprints = db.query(Blueprint).all()
            if not blueprints:
                return "No blueprints available."
            lines = [f"• {b.id} — {b.display_name}" for b in blueprints]
            return "Available blueprints:\n" + "\n".join(lines)

        if cmd == "/bind":
            if len(parts) < 2:
                return "Usage: /bind <blueprint_id> [--trigger always|mention|prefix] [--prefix <word>]"
            blueprint_id = parts[1]
            blueprint = db.query(Blueprint).filter_by(id=blueprint_id).first()
            if not blueprint:
                ids = [b.id for b in db.query(Blueprint).all()]
                return f"Blueprint '{blueprint_id}' not found. Available: {', '.join(ids)}"

            trigger_type = "always"
            trigger_prefix = None
            if "--trigger" in parts:
                idx = parts.index("--trigger")
                if idx + 1 < len(parts):
                    trigger_type = parts[idx + 1]
            if trigger_type not in _VALID_TRIGGERS:
                return f"Invalid trigger type '{trigger_type}'. Must be one of: always, mention, prefix."
            if "--prefix" in parts:
                idx = parts.index("--prefix")
                if idx + 1 < len(parts):
                    trigger_prefix = parts[idx + 1]

            # Clear conversation history on rebind
            db.query(ConversationHistory).filter_by(group_id=group_jid).delete()

            existing = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
            if existing:
                existing.blueprint_id = blueprint_id
                existing.status = "active"
                existing.trigger_type = trigger_type
                existing.trigger_prefix = trigger_prefix
                existing.bound_at = datetime.now(timezone.utc)
            else:
                db.add(GroupRegistry(
                    group_jid=group_jid,
                    blueprint_id=blueprint_id,
                    status="active",
                    trigger_type=trigger_type,
                    trigger_prefix=trigger_prefix,
                ))
            db.commit()
            return f"Bound '{blueprint.display_name}' to this group (trigger: {trigger_type})."

        if cmd == "/unbind":
            deleted = db.query(GroupRegistry).filter_by(group_jid=group_jid).delete()
            db.commit()
            return "Agent unbound from this group." if deleted else "No agent was bound to this group."

        if cmd == "/pause":
            entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
            if not entry:
                return "No agent is bound to this group."
            entry.status = "paused"
            db.commit()
            return "Agent paused."

        if cmd == "/resume":
            entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
            if not entry:
                return "No agent is bound to this group."
            entry.status = "active"
            db.commit()
            return "Agent resumed."

        if cmd == "/sync":
            entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
            if not entry:
                return "No agent is bound to this group."
            if not self._bridge_url:
                return "Bridge URL not configured — cannot fetch group description."
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{self._bridge_url}/group-meta/{group_jid}")
                    resp.raise_for_status()
                    description = resp.json().get("description", "").strip()
            except Exception as exc:
                return f"Failed to fetch group description: {exc}"
            entry.custom_instructions = description or None
            db.commit()
            if description:
                preview = description[:80] + ("…" if len(description) > 80 else "")
                return f"Custom instructions synced: \"{preview}\""
            return "Group description is empty — custom instructions cleared."

        return f"Unknown command: {parts[0]}. Try /blueprints."

    def _is_admin(self, db: Session, phone: str) -> bool:
        return db.query(AdminNumbers).filter_by(phone_number=phone).first() is not None
