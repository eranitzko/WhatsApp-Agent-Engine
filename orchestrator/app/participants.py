"""Builds the per-group participant system-prompt block for AgentRunner."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import GroupParticipant


def _acl_admin_phones(db: Session) -> set[str]:
    """All phones (canonical or known LID) that resolve to a bot-ACL admin.

    Cross-references AdminNumbers with UserProfile.known_lid so a participant
    row whose `phone` is actually a WhatsApp LID (common in shared groups —
    see migration 019) is still recognized as an admin when their LID is
    known, not just when GroupParticipant.phone happens to be their canonical
    number.
    """
    from app.db.models import AdminNumbers, UserProfile

    admin_numbers = {a.phone_number for a in db.query(AdminNumbers).all()}
    admin_phones = set(admin_numbers)
    for p in db.query(UserProfile).filter(UserProfile.known_lid.isnot(None)).all():
        if p.phone in admin_numbers:
            admin_phones.add(p.known_lid)
    return admin_phones


def _lid_to_canonical_map(db: Session) -> dict[str, str]:
    """Map every known WhatsApp LID to its owner's canonical phone.

    Used so the participant block displays the SAME phone format
    agent_runner injects as "Sender phone: X" (always the resolved canonical
    phone). Without this, a participant whose GroupParticipant.phone is a LID
    (shared groups) can never be matched by the model against the actual
    sender of the current message — it has no way to know that LID
    "175715853041683" and canonical phone "972523206175" are the same person,
    so it falls back to matching whatever phone-like token IS in both
    places, e.g. an @-mentioned LID — misattributing messages to the wrong
    person. This was a root cause of an observed identity mix-up.
    """
    from app.db.models import UserProfile

    return {
        p.known_lid: p.phone
        for p in db.query(UserProfile).filter(UserProfile.known_lid.isnot(None)).all()
    }


def build_participant_block(db: Session, group_jid: str) -> str | None:
    """Return a formatted participant list for injection into the system prompt.

    Includes removed members so the agent can still reference them by name.
    Marks which members are actual bot-ACL admins (per AdminNumbers) so the
    agent never has to guess who to direct a user to for admin-only actions —
    previously it would name arbitrary group members with no admin rights at
    all, since this block gave no admin/non-admin signal.
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

    admin_phones = _acl_admin_phones(db)
    lid_to_canonical = _lid_to_canonical_map(db)

    lines = []
    if rows:
        for r in rows:
            display = r.admin_name or r.push_name or r.phone
            prefix = "(removed) " if r.status == "removed" else ""
            suffix = " (admin)" if r.phone in admin_phones else ""
            shown_phone = lid_to_canonical.get(r.phone, r.phone)
            lines.append(f"- {prefix}{display}{suffix}: {shown_phone}")
        block = "Family members in this group:\n" + "\n".join(lines)
    else:
        block = "Family members in this group: (none recorded)"

    from app.db.models import HouseholdMember

    joint_phones = {
        m.phone for m in
        db.query(HouseholdMember).filter(
            HouseholdMember.phone.in_({r.phone for r in rows}),
            HouseholdMember.ledger_mode == "joint",
        ).all()
    }
    active_household = [
        (r.admin_name or r.push_name or r.phone)
        for r in rows
        if r.phone in joint_phones and r.status == "active"
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

    # Include both the raw phone/LID and its canonical equivalent (if known)
    # so a person already shown in the main list (now under their canonical
    # phone — see lid_to_canonical above) isn't duplicated below under that
    # same canonical phone.
    group_phones: set[str] = {r.phone for r in rows} | {
        lid_to_canonical[r.phone] for r in rows if r.phone in lid_to_canonical
    }
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
        admin_suffix = " (admin)" if ua.phone in admin_phones else ""
        known_lines.append(f"- {name or ua.phone}{admin_suffix}: {ua.phone}")

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
        known_lines.append(f"- {label or an.phone_number} (admin): {an.phone_number}")

    if known_lines:
        block += "\n\nKnown counterparties (other registered users):\n" + "\n".join(known_lines)
    elif not rows:
        return None  # truly nothing to show

    return block
