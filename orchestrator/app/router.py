from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry


class Router:
    def resolve(self, db: Session, group_jid: str) -> tuple[Blueprint | None, GroupRegistry | None]:
        entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
        if not entry or entry.status != "active":
            return None, None
        blueprint = db.query(Blueprint).filter_by(id=entry.blueprint_id).first()
        return blueprint, entry

    def check_trigger(self, entry: GroupRegistry, text: str, bot_phone: str) -> bool:
        if entry.trigger_type == "always":
            return True
        if entry.trigger_type == "mention":
            return f"@{bot_phone}" in (text or "")
        if entry.trigger_type == "prefix":
            return (text or "").startswith(entry.trigger_prefix or "")
        return False
