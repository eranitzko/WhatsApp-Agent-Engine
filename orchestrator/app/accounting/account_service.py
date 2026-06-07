"""Central coordination service for personal accounting cross-group operations."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app import bridge_client
from app.db.models import (
    AdminNumbers, CrossGroupConfirmation, GroupRegistry, UserAccount, UserProfile,
)

logger = logging.getLogger(__name__)

_DEFAULT_CONFIRMATION_TIMEOUT_HOURS = 24


class AccountService:
    # ── User / group resolution ───────────────────────────────────────────────

    def resolve_user(self, db: Session, phone: str) -> UserAccount | None:
        return db.query(UserAccount).filter_by(phone=phone, role="owner").first()

    def resolve_group_owner(self, db: Session, group_jid: str) -> str | None:
        row = db.query(UserAccount).filter_by(group_jid=group_jid, role="owner").first()
        return row.phone if row else None

    def get_group_members(self, db: Session, group_jid: str) -> list[str]:
        rows = db.query(UserAccount).filter_by(group_jid=group_jid).all()
        return [r.phone for r in rows]

    def get_display_name(self, db: Session, phone: str) -> str:
        row = db.query(UserProfile).filter_by(phone=phone).first()
        if row and row.display_name:
            return row.display_name
        return phone

    def is_sys_admin(self, db: Session, phone: str) -> bool:
        return db.query(AdminNumbers).filter_by(phone_number=phone).first() is not None

    def get_group_type(self, db: Session, group_jid: str) -> str:
        row = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
        if row is None:
            return "unregistered"
        return row.group_type or "unregistered"

    def get_personal_group_jid(self, db: Session, phone: str) -> str | None:
        acct = self.resolve_user(db, phone)
        return acct.group_jid if acct else None

    def _confirmation_timeout_hours(self, db: Session) -> int:
        from app.db.models import SystemConfig
        row = db.query(SystemConfig).filter_by(
            key="cross_group_confirmation_timeout_hours"
        ).first()
        if row:
            try:
                return int(row.value)
            except ValueError:
                pass
        return _DEFAULT_CONFIRMATION_TIMEOUT_HOURS

    # ── Cross-group notifications ─────────────────────────────────────────────

    async def notify_user(self, db: Session, target_phone: str, message: str) -> None:
        target_jid = self.get_personal_group_jid(db, target_phone)
        if not target_jid:
            logger.warning("notify_user: no personal group for %s", target_phone)
            return
        try:
            await bridge_client.send_message(target_jid, message)
        except Exception:
            logger.exception("notify_user: failed to send to %s (%s)", target_phone, target_jid)

    async def notify_all_in_group(self, db: Session, group_jid: str, message: str) -> None:
        try:
            await bridge_client.send_message(group_jid, message)
        except Exception:
            logger.exception("notify_all_in_group: failed to send to %s", group_jid)

    # ── Confirmation lifecycle ────────────────────────────────────────────────

    async def request_confirmation(
        self,
        db: Session,
        initiator_phone: str,
        initiator_group_jid: str,
        target_phone: str,
        action_type: str,
        action_payload: dict,
        confirmation_message: str,
        split_transaction_id: str | None = None,
    ) -> CrossGroupConfirmation:
        target_jid = self.get_personal_group_jid(db, target_phone)
        if not target_jid:
            raise ValueError(f"No personal group found for {target_phone}")

        timeout_hours = self._confirmation_timeout_hours(db)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=timeout_hours)

        conf = CrossGroupConfirmation(
            split_transaction_id=split_transaction_id,
            initiator_phone=initiator_phone,
            initiator_group_jid=initiator_group_jid,
            target_phone=target_phone,
            target_group_jid=target_jid,
            action_type=action_type,
            action_payload=json.dumps(action_payload),
            status="pending",
            expires_at=expires_at,
        )
        db.add(conf)
        db.commit()
        db.refresh(conf)

        await bridge_client.send_message(target_jid, confirmation_message)
        return conf

    def handle_confirmation_reply(
        self,
        db: Session,
        group_jid: str,
        phone: str,
        reply: str,
    ) -> bool:
        """Returns True if a pending confirmation was resolved, False if none found."""
        now = datetime.now(timezone.utc)
        conf = (
            db.query(CrossGroupConfirmation)
            .filter_by(target_phone=phone, target_group_jid=group_jid, status="pending")
            .filter(CrossGroupConfirmation.expires_at > now)
            .order_by(CrossGroupConfirmation.created_at.asc())
            .first()
        )
        if conf is None:
            return False

        reply_lower = reply.strip().lower()
        if reply_lower in ("yes", "כן", "y", "אישור"):
            conf.status = "confirmed"
        elif reply_lower in ("no", "לא", "n", "ביטול"):
            conf.status = "rejected"
        else:
            return False

        db.commit()
        return True
