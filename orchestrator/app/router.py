import time
from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry

_TTL = 300  # seconds — blueprint bindings rarely change


class Router:
    def __init__(self):
        # group_jid -> (blueprint | None, entry | None, monotonic_timestamp)
        self._cache: dict[str, tuple] = {}

    def resolve(self, db: Session, group_jid: str) -> tuple[Blueprint | None, GroupRegistry | None]:
        """Return (blueprint, entry) for the group, using a 5-min in-process cache.

        Cache entries are invalidated explicitly via invalidate() whenever a
        command modifies the group's binding (/bind, /unbind, /pause, /resume).
        """
        cached = self._cache.get(group_jid)
        if cached and (time.monotonic() - cached[2]) < _TTL:
            return cached[0], cached[1]

        entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
        if not entry or entry.status != "active":
            self._cache[group_jid] = (None, None, time.monotonic())
            return None, None

        blueprint = db.query(Blueprint).filter_by(id=entry.blueprint_id).first()
        self._cache[group_jid] = (blueprint, entry, time.monotonic())
        return blueprint, entry

    def invalidate(self, group_jid: str) -> None:
        """Evict a group's cached route. Call after any /bind, /unbind, /pause, /resume."""
        self._cache.pop(group_jid, None)

    def check_trigger(self, entry: GroupRegistry, text: str, bot_phone: str) -> bool:
        if entry.trigger_type == "always":
            return True
        if entry.trigger_type == "mention":
            return f"@{bot_phone}" in (text or "")
        if entry.trigger_type == "prefix":
            prefix = entry.trigger_prefix
            if not prefix:
                return False
            return (text or "").startswith(prefix)
        return False
