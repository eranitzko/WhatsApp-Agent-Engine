"""Per-group conversation history for Claude — persisted to SQLite.

Context survives process restarts (deploys, crashes, watchdog restarts).
Resets after IDLE_MINUTES of inactivity, same as before.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from app.db.models import ConversationHistory
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

MAX_TURNS    = 8   # user+assistant pairs kept
IDLE_MINUTES = 60  # inactivity window before context resets


class GroupContext:
    """In-process view of a group's conversation history.

    Reads from DB on construction; writes back on every add/reset.
    """

    def __init__(self, group_id: str) -> None:
        self.group_id = group_id
        self.messages: list[dict] = []
        self.last_active: datetime = datetime.now(timezone.utc)
        self._load()

    # ── Persistence helpers ───────────────────────────────────────────────────

    def _load(self) -> None:
        """Load state from DB. Creates a fresh row if none exists."""
        try:
            with SessionLocal() as db:
                row = db.get(ConversationHistory, self.group_id)
                if row is None:
                    return  # fresh context, nothing to load
                last_active = row.last_active
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last_active > timedelta(minutes=IDLE_MINUTES):
                    # Stale — drop it from DB and start fresh
                    db.delete(row)
                    db.commit()
                    return
                self.messages = json.loads(row.messages_json or "[]")
                self.last_active = last_active
        except Exception:
            logger.exception("Failed to load conversation history for %s", self.group_id)

    def _save(self) -> None:
        """Upsert current state to DB."""
        try:
            with SessionLocal() as db:
                row = db.get(ConversationHistory, self.group_id)
                if row is None:
                    row = ConversationHistory(group_id=self.group_id)
                    db.add(row)
                row.messages_json = json.dumps(self.messages, ensure_ascii=False)
                row.last_active   = self.last_active
                db.commit()
        except Exception:
            logger.exception("Failed to save conversation history for %s", self.group_id)

    # ── Public API ────────────────────────────────────────────────────────────

    def is_stale(self) -> bool:
        return datetime.now(timezone.utc) - self.last_active > timedelta(minutes=IDLE_MINUTES)

    def touch(self) -> None:
        self.last_active = datetime.now(timezone.utc)

    def add(self, role: str, content) -> None:
        """Append a message, trim to MAX_TURNS pairs, and persist."""
        self.messages.append({"role": role, "content": content})
        max_messages = MAX_TURNS * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]
        self.touch()
        self._save()

    def reset(self) -> None:
        """Clear history and remove DB row."""
        self.messages = []
        self.touch()
        try:
            with SessionLocal() as db:
                row = db.get(ConversationHistory, self.group_id)
                if row:
                    db.delete(row)
                    db.commit()
        except Exception:
            logger.exception("Failed to reset conversation history for %s", self.group_id)


class ContextStore:
    """Thin cache over DB-backed GroupContexts.

    A GroupContext is kept in-process for the lifetime of a request sequence.
    On cache miss (or after an idle reset), it's loaded fresh from the DB.
    """

    def __init__(self) -> None:
        self._cache: dict[str, GroupContext] = {}

    def get(self, group_id: str) -> GroupContext:
        ctx = self._cache.get(group_id)
        if ctx is None or ctx.is_stale():
            ctx = GroupContext(group_id)
            self._cache[group_id] = ctx
        return ctx

    def reset(self, group_id: str) -> None:
        ctx = self._cache.pop(group_id, None)
        if ctx:
            ctx.reset()
        else:
            # Reset in DB even if not cached
            GroupContext(group_id).reset()


# Singleton used by the agent
context_store = ContextStore()
