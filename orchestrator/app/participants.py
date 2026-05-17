"""Builds the per-group participant system-prompt block for AgentRunner."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import GroupParticipant


def build_participant_block(db: Session, group_jid: str) -> str | None:
    """Return a formatted participant list for injection into the system prompt.

    Includes removed members so the agent can still reference them by name.
    Returns None if no participants are recorded for the group.
    """
    rows = (
        db.query(GroupParticipant)
        .filter_by(group_jid=group_jid)
        .order_by(GroupParticipant.joined_at)
        .all()
    )
    if not rows:
        return None

    lines = []
    for r in rows:
        display = r.admin_name or r.push_name or r.phone
        prefix = "(removed) " if r.status == "removed" else ""
        lines.append(f"- {prefix}{display}: {r.phone}")

    block = "Family members in this group:\n" + "\n".join(lines)

    active_household = [
        (r.admin_name or r.push_name or r.phone)
        for r in rows
        if r.is_household and r.status == "active"
    ]
    if len(active_household) >= 2:
        names_str = " and ".join(active_household)
        block += (
            f"\n\nShared household: {names_str} share a single account "
            f"(shown as \"Parents\" in reports and balances). "
            f"Do not track or report debts between them."
        )

    return block
