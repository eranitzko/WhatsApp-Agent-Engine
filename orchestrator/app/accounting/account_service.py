"""Central coordination service for personal accounting cross-group operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.db.models import (
    AdminNumbers, GroupRegistry, UserAccount, UserProfile,
)

if TYPE_CHECKING:
    pass

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
