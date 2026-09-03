"""Handles bot group-join events and sys-admin approval flow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import bridge_client
from app.agent.reply_words import is_affirmative, is_negative
from app.db.models import (
    AdminNumbers, GroupRegistry, UserAccount, Blueprint, UserProfile,
)

logger = logging.getLogger(__name__)

_DEFAULT_BLUEPRINT_ID = "family_accounting"


class GroupRegistrationHandler:
    def __init__(self) -> None:
        # key = target_group_jid; value = pending registration dict
        self._pending: dict[str, dict] = {}

    async def on_bot_added_to_group(
        self,
        db: Session,
        group_jid: str,
        human_phones: list[str],
    ) -> None:
        if not human_phones:
            logger.info("Bot added to empty group %s — ignoring", group_jid)
            return

        # Ensure blueprint exists
        _ensure_blueprint(db)

        # Check if all members are sys-admins
        admin_phones = {
            r.phone_number for r in db.query(AdminNumbers).all()
        }
        all_admins = all(p in admin_phones for p in human_phones)

        if all_admins:
            await self._register_group(
                db, group_jid, human_phones, "sys_admin",
                welcome="I'm ready. As a system admin you have full access to all accounts."
            )
            return

        # Determine candidate group type
        if len(human_phones) == 1:
            group_type_candidate = "personal"
        else:
            group_type_candidate = "shared"

        # Register as unregistered and notify sys-admins
        _ensure_group_registry(db, group_jid, "unregistered")

        sys_admin_groups = self._get_sys_admin_group_jids(db)
        if not sys_admin_groups:
            logger.warning("No sys-admin groups registered; cannot request approval for %s", group_jid)
            return

        phone_list = ", ".join(human_phones)
        msg = (
            f"{'Someone' if len(human_phones) == 1 else 'A group'} added me "
            f"({phone_list}). Register as their {group_type_candidate} account? (yes / no)\n"
            f"Group: {group_jid}"
        )

        self._pending[group_jid] = {
            "human_phones": human_phones,
            "group_type": group_type_candidate,
            "sys_admin_jids": sys_admin_groups,
            "created_at": datetime.now(timezone.utc),
        }

        for admin_jid in sys_admin_groups:
            try:
                await bridge_client.send_message(admin_jid, msg)
            except Exception:
                logger.exception("Failed to notify admin group %s about %s", admin_jid, group_jid)

    async def handle_admin_reply(
        self,
        db: Session,
        admin_group_jid: str,
        reply: str,
    ) -> bool:
        """Returns True if this reply resolved a pending registration."""
        target_jid = self._find_pending_for_admin(admin_group_jid)
        if target_jid is None:
            return False

        pending = self._pending.pop(target_jid)

        if is_affirmative(reply):
            await self._register_group(
                db, target_jid, pending["human_phones"], pending["group_type"],
                welcome="Your account is ready. You can start recording transactions here."
            )
            # Notify other admins who got the request
            for jid in pending["sys_admin_jids"]:
                if jid != admin_group_jid:
                    try:
                        await bridge_client.send_message(
                            jid, f"Registration for {target_jid} was approved by another admin."
                        )
                    except Exception:
                        pass
            return True

        if is_negative(reply):
            db.query(GroupRegistry).filter_by(group_jid=target_jid).delete()
            db.commit()
            try:
                await bridge_client.send_message(
                    target_jid, "This group was not approved. I'll be leaving now."
                )
            except Exception:
                pass
            return True

        return False

    def is_pending_reply(self, db: Session, admin_group_jid: str, text: str) -> bool:
        """True if this group has a pending registration and text is yes/no."""
        if self._find_pending_for_admin(admin_group_jid) is None:
            return False
        return is_affirmative(text) or is_negative(text)

    def get_pending_description(self, admin_group_jid: str) -> str | None:
        """Read-only peek at the pending registration request for this admin
        group, if any — lets a free-form reply (e.g. "לאשר") that doesn't
        exact-match is_pending_reply's word list still be classified with
        context, instead of silently falling through with the agent none the
        wiser that a registration was ever asked about."""
        target_jid = self._find_pending_for_admin(admin_group_jid)
        if target_jid is None:
            return None
        pending = self._pending[target_jid]
        phone_list = ", ".join(pending["human_phones"])
        return (
            f"Approve registering group {target_jid} ({phone_list}) as a "
            f"{pending['group_type']} account?"
        )

    def _find_pending_for_admin(self, admin_group_jid: str) -> str | None:
        for target_jid, info in self._pending.items():
            if admin_group_jid in info["sys_admin_jids"]:
                return target_jid
        return None

    def _get_sys_admin_group_jids(self, db: Session) -> list[str]:
        rows = db.query(GroupRegistry).filter_by(group_type="sys_admin").all()
        return [r.group_jid for r in rows]

    async def _register_group(
        self,
        db: Session,
        group_jid: str,
        human_phones: list[str],
        group_type: str,
        welcome: str,
    ) -> None:
        _ensure_blueprint(db)
        _ensure_group_registry(db, group_jid, group_type)

        for phone in human_phones:
            role = "owner" if len(human_phones) == 1 else "member"
            existing = db.query(UserAccount).filter_by(phone=phone, group_jid=group_jid).first()
            if not existing:
                db.add(UserAccount(phone=phone, group_jid=group_jid, role=role))

            if group_type == "personal":
                # Auto-link HouseholdMember.private_group_jid (household-enrolled users)
                _autolink_household_member(db, phone, group_jid)
                # Always upsert UserProfile.private_group_jid so LID-safe inbound
                # resolution works for everyone, even before household enrollment.
                _upsert_profile_private_group(db, phone, group_jid)

        db.commit()

        try:
            await bridge_client.send_message(group_jid, welcome)
        except Exception:
            logger.exception("Failed to send welcome to %s", group_jid)


def _autolink_household_member(db: Session, phone: str, group_jid: str) -> None:
    """Set HouseholdMember.private_group_jid when a personal group is approved.

    If a HouseholdMember row already exists for this phone (created via the
    admin panel during household setup) but has no private_group_jid yet, link
    it now.  If there is no HouseholdMember row, do nothing — the admin panel
    household setup must happen first.
    """
    from app.db.models import HouseholdMember
    member = db.query(HouseholdMember).filter_by(phone=phone).first()
    if member and member.private_group_jid is None:
        member.private_group_jid = group_jid
        logger.info(
            "auto-linked HouseholdMember phone=%s to private_group_jid=%s", phone, group_jid
        )


def _upsert_profile_private_group(db: Session, phone: str, group_jid: str) -> None:
    """Set UserProfile.private_group_jid so resolve_inbound is LID-safe for everyone.

    This runs on every personal-group registration, ensuring the mapping exists
    regardless of whether the person has been enrolled in a household.
    Only sets the field if it is not already populated (first group wins, matching
    the first-registered convention for primary routing).
    """
    profile = db.query(UserProfile).filter_by(phone=phone).first()
    if profile is None:
        db.add(UserProfile(phone=phone, private_group_jid=group_jid))
        logger.info("created UserProfile phone=%s with private_group_jid=%s", phone, group_jid)
    elif profile.private_group_jid is None:
        profile.private_group_jid = group_jid
        logger.info("set UserProfile.private_group_jid phone=%s → %s", phone, group_jid)


def _ensure_blueprint(db: Session) -> None:
    if not db.query(Blueprint).filter_by(id=_DEFAULT_BLUEPRINT_ID).first():
        logger.warning("Blueprint %s not found — group registration may fail", _DEFAULT_BLUEPRINT_ID)


def _ensure_group_registry(db: Session, group_jid: str, group_type: str) -> GroupRegistry:
    existing = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
    if existing:
        existing.group_type = group_type
    else:
        existing = GroupRegistry(
            group_jid=group_jid,
            blueprint_id=_DEFAULT_BLUEPRINT_ID,
            group_type=group_type,
            status="active",
        )
        db.add(existing)
    db.commit()
    return existing
