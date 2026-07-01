"""Per-group conversation history for Claude — persisted to SQLite.

Context survives process restarts (deploys, crashes, watchdog restarts).
Resets after IDLE_MINUTES of inactivity, same as before.

Tool-call context:
  Tool use/result blocks are stored alongside text turns so Claude can
  reference data from tool results (e.g. invoice UUIDs) in later turns,
  exactly like a chat window.  ``add_turn`` adds a complete logical turn
  atomically and trims at turn boundaries so the history never contains
  orphaned tool_use or tool_result blocks.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from app.db.models import ConversationHistory
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

MAX_TURNS    = 8   # logical turns kept (each turn = user msg + tool exchanges + assistant reply)
IDLE_MINUTES = 60  # inactivity window before context resets


def _is_turn_start(msg: dict) -> bool:
    """Return True when *msg* opens a new logical turn (human text, not a tool-result message).

    A logical turn starts with a user text message.  Tool-result messages
    also have role="user" but their content is a list of tool_result blocks —
    those are mid-turn, not turn starts.
    """
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            return first.get("type") != "tool_result"
    return True  # empty or unknown format — treat as turn start


def _trim_from_index(messages: list[dict], effective_pairs: int) -> int:
    """Return the index to slice *messages* from so at most *effective_pairs*
    complete logical turns remain, always cutting at a turn-start boundary.

    A naive count-based slice (``messages[-n:]``) can land between an
    assistant tool_use block and its paired user tool_result block, orphaning
    the tool_result — which the Anthropic API rejects with "unexpected
    tool_use_id". This must be used for every read AND write path that
    trims the message list (get_history, add, add_turn), not just writes,
    since a bad slice on read produces the identical corrupted payload.
    Returns 0 (no trim) when the turn count is already within budget.
    """
    turn_start_indices = [i for i, m in enumerate(messages) if _is_turn_start(m)]
    if len(turn_start_indices) > effective_pairs:
        return turn_start_indices[-effective_pairs]
    return 0


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

    def get_history(self, max_pairs: int | None = None, idle_minutes: int | None = None) -> list[dict]:
        """Return the current message list, applying optional per-call overrides.

        Args:
            max_pairs: If provided, return only the last ``max_pairs`` user+assistant
                pairs (i.e. ``max_pairs * 2`` messages).  Defaults to ``MAX_TURNS``.
            idle_minutes: If provided, treat the context as stale (empty) when
                inactivity exceeds this many minutes instead of the module default.
        """
        effective_idle = idle_minutes if idle_minutes is not None else IDLE_MINUTES
        if datetime.now(timezone.utc) - self.last_active > timedelta(minutes=effective_idle):
            return []
        effective_pairs = max_pairs if max_pairs is not None else MAX_TURNS
        trim_from = _trim_from_index(self.messages, effective_pairs)
        return list(self.messages[trim_from:])

    def add(self, role: str, content, max_pairs: int | None = None) -> None:
        """Append a message, trim at a turn boundary, and persist.

        Args:
            role: ``"user"`` or ``"assistant"``.
            content: Message content (string or list of content blocks).
            max_pairs: If provided, trim to this many logical turns instead of
                the module-level ``MAX_TURNS`` default.
        """
        self.messages.append({"role": role, "content": content})
        effective_pairs = max_pairs if max_pairs is not None else MAX_TURNS
        trim_from = _trim_from_index(self.messages, effective_pairs)
        if trim_from:
            self.messages = self.messages[trim_from:]
        self.touch()
        self._save()

    def add_turn(self, turn_messages: list[dict], max_pairs: int | None = None) -> None:
        """Add a complete logical turn atomically and trim at turn boundaries.

        *turn_messages* must be the full sequence for one turn:
            [user_text_msg, asst_tool_use_msg?, user_tool_result_msg?, ..., asst_text_msg]

        Trimming removes the oldest complete turns so the stored history never
        has orphaned tool_use / tool_result blocks that would cause API errors.
        *max_pairs* here means "number of logical turns to keep."
        """
        self.messages.extend(turn_messages)
        effective_pairs = max_pairs if max_pairs is not None else MAX_TURNS
        trim_from = _trim_from_index(self.messages, effective_pairs)
        if trim_from:
            self.messages = self.messages[trim_from:]
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

    # ── Convenience delegation methods ────────────────────────────────────────

    def get_history(self, group_id: str, max_pairs: int | None = None, idle_minutes: int | None = None) -> list:
        return self.get(group_id).get_history(max_pairs=max_pairs, idle_minutes=idle_minutes)

    def add(self, group_id: str, role: str, content, max_pairs: int | None = None) -> None:
        self.get(group_id).add(role=role, content=content, max_pairs=max_pairs)

    def add_turn(self, group_id: str, turn_messages: list[dict], max_pairs: int | None = None) -> None:
        self.get(group_id).add_turn(turn_messages, max_pairs=max_pairs)

    def clear(self, group_id: str) -> None:
        ctx = self._cache.get(group_id)
        if ctx:
            ctx.reset()  # clears DB row
            del self._cache[group_id]


