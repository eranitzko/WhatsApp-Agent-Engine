"""Builds the per-group participant system-prompt block for AgentRunner."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import GroupParticipant


def build_participant_block(db: Session, group_jid: str) -> str | None:
    """Return a formatted participant list for injection into the system prompt.

    Includes removed members so the agent can still reference them by name.
    Also appends "Known counterparties" — users registered in the system from
    other groups — so the agent can resolve names even for cross-group accounting.
    Returns None if no participants are recorded for the group.
    """
    rows = (
        db.query(GroupParticipant)
        .filter_by(group_jid=group_jid)
        .order_by(GroupParticipant.joined_at)
        .all()
    )

    lines = []
    if rows:
        for r in rows:
            display = r.admin_name or r.push_name or r.phone
            prefix = "(removed) " if r.status == "removed" else ""
            lines.append(f"- {prefix}{display}: {r.phone}")
        block = "Family members in this group:\n" + "\n".join(lines)
    else:
        block = "Family members in this group: (none recorded)"

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

    # Add known counterparties: users registered in the system from other groups.
    # This lets the agent resolve names like "Sivan" to a phone even when that
    # person's personal group is separate from the current group.
    from app.db.models import AdminNumbers, UserAccount, UserProfile

    group_phones: set[str] = {r.phone for r in rows}
    seen: set[str] = set(group_phones)
    known_lines: list[str] = []

    # Users with a registered personal account
    all_owners = db.query(UserAccount).filter_by(role="owner").all()
    for ua in all_owners:
        if ua.phone in seen:
            continue
        seen.add(ua.phone)
        name: str | None = None
        profile = db.get(UserProfile, ua.phone)
        if profile and profile.display_name:
            name = profile.display_name
        if not name:
            admin_row = db.get(AdminNumbers, ua.phone)
            if admin_row and admin_row.label and admin_row.label != "owner":
                name = admin_row.label
        if not name:
            # Fall back to name from their own group's participant record
            gp = db.get(GroupParticipant, (ua.group_jid, ua.phone))
            if gp:
                name = gp.admin_name or gp.push_name
        known_lines.append(f"- {name or ua.phone}: {ua.phone}")

    # Collect first names already visible in the group (to avoid duplicating people
    # whose GroupParticipant.phone is a WhatsApp LID rather than a human phone).
    shown_first_names = {
        (r.admin_name or r.push_name or "").split()[0].lower()
        for r in rows
        if (r.admin_name or r.push_name)
    }

    # Admin numbers not covered by a UserAccount (e.g. pending registration)
    for an in db.query(AdminNumbers).all():
        if an.phone_number in seen:
            continue
        label = an.label if (an.label and an.label != "owner") else None
        # Skip if this person is already shown under their push_name (LID mismatch case)
        if label and label.split()[0].lower() in shown_first_names:
            seen.add(an.phone_number)  # mark as seen so UserAccount loop also skips
            continue
        seen.add(an.phone_number)
        known_lines.append(f"- {label or an.phone_number}: {an.phone_number}")

    if known_lines:
        block += "\n\nKnown counterparties (other registered users):\n" + "\n".join(known_lines)
    elif not rows:
        return None  # truly nothing to show

    return block
