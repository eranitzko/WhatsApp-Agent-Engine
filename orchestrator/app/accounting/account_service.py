"""Central coordination service for personal accounting cross-group operations."""

from __future__ import annotations

import json
import logging
import uuid as _uuid_mod
from datetime import datetime, timezone, timedelta, date as _date
from decimal import Decimal
from sqlalchemy.orm import Session

from app import bridge_client
from app.db.models import (
    AdminNumbers, CrossGroupConfirmation, GroupRegistry, LedgerEntry,
    LedgerSettlement, SplitTransaction, UserAccount, UserProfile,
)
from app.tools.accounting_fifo import DebtLeg, apply_payment

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

    # ── Transaction processing ────────────────────────────────────────────────

    def _is_first_party(self, reporter_phone: str, debtor_phone: str) -> bool:
        """True when reporter is voluntarily taking on debt (1st-party action)."""
        return reporter_phone == debtor_phone

    async def process_transaction(
        self,
        db: Session,
        reporter_phone: str,
        reporter_group_jid: str,
        payer_phone: str,
        debtor_phone: str,
        amount_ils: Decimal,
        description: str,
        transaction_date,
        split_transaction_id: str | None = None,
    ) -> str:
        from app.db.models import LedgerEntry
        import uuid as _uuid_mod

        payer_name = self.get_display_name(db, payer_phone)
        debtor_name = self.get_display_name(db, debtor_phone)

        if self._is_first_party(reporter_phone, debtor_phone):
            # Debtor is self-reporting → write immediately
            entry = LedgerEntry(
                transaction_id=str(_uuid_mod.uuid4()),
                group_jid=reporter_group_jid,
                from_phone=debtor_phone,
                to_phone=payer_phone,
                amount_ils=amount_ils,
                description=description,
                transaction_date=transaction_date,
            )
            db.add(entry)
            db.commit()

            notify_msg = (
                f"{debtor_name} acknowledged a ₪{float(amount_ils):.2f} debt to you "
                f"({description}). Your balance has been updated."
            )
            await self.notify_user(db, payer_phone, notify_msg)
            return f"Recorded. {payer_name} has been notified."
        else:
            # Reporter is creditor claiming debt on debtor's behalf → confirmation needed
            confirm_msg = (
                f"{payer_name} says you owe ₪{float(amount_ils):.2f} ({description}). "
                f"Confirm? (yes / no)"
            )
            await self.request_confirmation(
                db=db,
                initiator_phone=reporter_phone,
                initiator_group_jid=reporter_group_jid,
                target_phone=debtor_phone,
                action_type="record_expense",
                action_payload={
                    "group_jid": reporter_group_jid,
                    "payer_phone": payer_phone,
                    "debtor_phone": debtor_phone,
                    "amount_ils": str(amount_ils),
                    "description": description,
                    "transaction_date": str(transaction_date),
                    "split_transaction_id": split_transaction_id,
                },
                confirmation_message=confirm_msg,
                split_transaction_id=split_transaction_id,
            )
            return f"Confirmation request sent to {debtor_name}. I'll notify you when they respond."

    # ── Payment FIFO settlement ───────────────────────────────────────────────

    async def _apply_payment_fifo(
        self,
        db: Session,
        group_jid: str,
        payer_phone: str,
        payee_phone: str,
        amount_ils: Decimal,
        payment_date: _date,
    ) -> str:
        """Apply FIFO settlement for a payment and return a summary string."""
        now = datetime.now(timezone.utc)
        open_rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                LedgerEntry.from_phone == payer_phone,
                LedgerEntry.to_phone == payee_phone,
                LedgerEntry.amount_ils > LedgerEntry.amount_settled_ils,
            )
            .order_by(LedgerEntry.transaction_date)
            .all()
        )
        debt_legs = [
            DebtLeg(
                id=r.id,
                amount_ils=r.amount_ils,
                amount_settled_ils=r.amount_settled_ils or Decimal("0"),
                transaction_date=r.transaction_date,
            )
            for r in open_rows
        ]
        result = apply_payment(amount_ils, debt_legs)
        for leg_id, new_settled in result.updated_legs:
            row = db.get(LedgerEntry, leg_id)
            if row:
                row.amount_settled_ils = new_settled
        payment_leg = LedgerEntry(
            transaction_id=str(_uuid_mod.uuid4()),
            group_jid=group_jid,
            from_phone=payer_phone,
            to_phone=payee_phone,
            amount_ils=amount_ils,
            amount_settled_ils=amount_ils,
            description=f"Payment on {payment_date.isoformat()}",
            transaction_date=payment_date,
            created_at=now,
        )
        db.add(payment_leg)
        db.flush()
        for debt_leg_id, applied_amount in result.settlements:
            db.add(LedgerSettlement(
                payment_leg_id=payment_leg.id,
                debt_leg_id=debt_leg_id,
                amount_ils=applied_amount,
                created_at=now,
            ))
        db.commit()
        parts = [f"{amt:.2f} ILS off {did[:8]}" for did, amt in result.settlements]
        summary = "; ".join(parts) if parts else "no open debts found"
        return f"Payment of {amount_ils:.2f} ILS recorded. {summary}."

    async def process_payment(
        self,
        db: Session,
        reporter_phone: str,
        reporter_group_jid: str,
        payer_phone: str,
        payee_phone: str,
        amount_ils: Decimal,
        payment_date: _date,
    ) -> str:
        """Route a payment through the correct path.

        First-party (payer self-reports): record immediately and notify payee.
        Second-party (payee reports): send a confirmation request to the payer's
        personal group and wait for their reply.
        """
        payer_name = self.get_display_name(db, payer_phone)
        payee_name = self.get_display_name(db, payee_phone)

        if self._is_first_party(reporter_phone, payer_phone):
            # Payer is self-reporting → record immediately
            summary = await self._apply_payment_fifo(
                db, reporter_group_jid, payer_phone, payee_phone, amount_ils, payment_date
            )
            notify_msg = (
                f"{payer_name} reported a ₪{float(amount_ils):.2f} payment to you. "
                f"Your balance has been updated."
            )
            await self.notify_user(db, payee_phone, notify_msg)
            return f"Recorded. {payee_name} has been notified."
        else:
            # Payee is reporting → payer must confirm in their own personal group
            confirm_msg = (
                f"{payee_name} says you paid them ₪{float(amount_ils):.2f} on {payment_date}. "
                f"Confirm? (yes / no)"
            )
            await self.request_confirmation(
                db=db,
                initiator_phone=reporter_phone,
                initiator_group_jid=reporter_group_jid,
                target_phone=payer_phone,
                action_type="record_payment",
                action_payload={
                    "group_jid": reporter_group_jid,
                    "payer_phone": payer_phone,
                    "payee_phone": payee_phone,
                    "amount_ils": str(amount_ils),
                    "payment_date": str(payment_date),
                },
                confirmation_message=confirm_msg,
            )
            return f"Confirmation request sent to {payer_name}. I'll notify you when they respond."

    async def commit_confirmed_transaction(self, db: Session, conf: CrossGroupConfirmation) -> None:
        """Write the ledger entry for a confirmed 2nd-party transaction."""
        payload = json.loads(conf.action_payload)

        if conf.action_type == "record_payment":
            # FIFO settlement for a confirmed payment
            payment_date = _date.fromisoformat(payload["payment_date"])
            amount_ils = Decimal(payload["amount_ils"])
            await self._apply_payment_fifo(
                db,
                payload["group_jid"],
                payload["payer_phone"],
                payload["payee_phone"],
                amount_ils,
                payment_date,
            )
            payer_name = self.get_display_name(db, payload["payer_phone"])
            payee_name = self.get_display_name(db, payload["payee_phone"])
            await self.notify_user(
                db, payload["payee_phone"],
                f"{payer_name} confirmed the ₪{float(amount_ils):.2f} payment."
            )
            await bridge_client.send_message(
                conf.target_group_jid,
                f"Confirmed. Your payment to {payee_name} has been recorded."
            )
            return

        # Default: record_expense — write a new ledger debt entry
        entry = LedgerEntry(
            transaction_id=str(_uuid_mod.uuid4()),
            group_jid=payload["group_jid"],
            from_phone=payload["debtor_phone"],
            to_phone=payload["payer_phone"],
            amount_ils=Decimal(payload["amount_ils"]),
            description=payload["description"],
            transaction_date=_date.fromisoformat(payload["transaction_date"]),
        )
        db.add(entry)
        db.commit()

        # Notify both parties
        debtor_name = self.get_display_name(db, payload["debtor_phone"])
        payer_name = self.get_display_name(db, payload["payer_phone"])
        await self.notify_user(
            db, payload["payer_phone"],
            f"{debtor_name} confirmed the ₪{float(entry.amount_ils):.2f} debt ({payload['description']})."
        )
        await bridge_client.send_message(
            conf.target_group_jid,
            f"Confirmed. Your balance with {payer_name} has been updated."
        )

    # ── Split transaction management ──────────────────────────────────────────

    async def process_split(
        self,
        db: Session,
        reporter_phone: str,
        reporter_group_jid: str,
        payer_phone: str,
        shares: list[dict],       # [{"phone": str, "amount_ils": Decimal}]
        total_amount: Decimal,
        description: str,
        transaction_date,
    ) -> SplitTransaction:
        split = SplitTransaction(
            reporter_group_jid=reporter_group_jid,
            reporter_phone=reporter_phone,
            payer_phone=payer_phone,
            total_amount=total_amount,
            description=description,
            status="pending",
        )
        db.add(split)
        db.flush()  # get split.id

        payer_name = self.get_display_name(db, payer_phone)

        for share in shares:
            phone = share["phone"]
            amount = share["amount_ils"]

            if phone == payer_phone:
                continue  # payer's share absorbed

            if self._is_first_party(reporter_phone, phone):
                # Reporter is acknowledging their own share — held as self_confirmed
                conf = CrossGroupConfirmation(
                    split_transaction_id=split.id,
                    initiator_phone=reporter_phone,
                    initiator_group_jid=reporter_group_jid,
                    target_phone=phone,
                    target_group_jid=reporter_group_jid,
                    action_type="split_share",
                    action_payload=json.dumps({
                        "group_jid": reporter_group_jid,
                        "payer_phone": payer_phone,
                        "debtor_phone": phone,
                        "amount_ils": str(amount),
                        "description": description,
                        "transaction_date": str(transaction_date),
                        "split_transaction_id": split.id,
                    }),
                    status="self_confirmed",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=self._confirmation_timeout_hours(db)),
                )
                db.add(conf)
            else:
                debtor_name = self.get_display_name(db, phone)
                confirm_msg = (
                    f"{debtor_name}, your share of a ₪{float(total_amount):.2f} "
                    f"{description} with {payer_name} is ₪{float(amount):.2f}. "
                    f"Confirm? (yes / no)"
                )
                await self.request_confirmation(
                    db=db,
                    initiator_phone=reporter_phone,
                    initiator_group_jid=reporter_group_jid,
                    target_phone=phone,
                    action_type="split_share",
                    action_payload={
                        "group_jid": reporter_group_jid,
                        "payer_phone": payer_phone,
                        "debtor_phone": phone,
                        "amount_ils": str(amount),
                        "description": description,
                        "transaction_date": str(transaction_date),
                        "split_transaction_id": split.id,
                    },
                    confirmation_message=confirm_msg,
                    split_transaction_id=split.id,
                )

        db.commit()
        return split

    async def handle_split_decline(
        self,
        db: Session,
        declined_conf: CrossGroupConfirmation,
    ) -> None:
        split_id = declined_conf.split_transaction_id
        if not split_id:
            return

        split = db.query(SplitTransaction).filter_by(id=split_id).first()
        if not split:
            return

        split.status = "suspended"

        # Pause all other pending confirmations
        db.query(CrossGroupConfirmation).filter_by(
            split_transaction_id=split_id, status="pending"
        ).update({"status": "paused"})
        db.commit()

        decliner_name = self.get_display_name(db, declined_conf.target_phone)
        reporter_name = self.get_display_name(db, split.reporter_phone)

        # Notify reporter
        await bridge_client.send_message(
            split.reporter_group_jid,
            f"{decliner_name} declined their share of the ₪{float(split.total_amount):.2f} "
            f"{split.description}. The split is suspended — re-submit if you agree on new amounts."
        )
        # Notify payer if different from reporter
        if split.payer_phone != split.reporter_phone:
            await self.notify_user(
                db, split.payer_phone,
                f"{decliner_name} declined their share of the ₪{float(split.total_amount):.2f} "
                f"{split.description} (reported by {reporter_name}). Transaction suspended."
            )

    async def finalize_split(self, db: Session, split: SplitTransaction) -> None:
        """Commit all ledger entries for a fully confirmed split."""
        confs = db.query(CrossGroupConfirmation).filter_by(
            split_transaction_id=split.id
        ).all()

        all_done = all(c.status in ("confirmed", "self_confirmed") for c in confs)
        if not all_done:
            return

        for conf in confs:
            await self.commit_confirmed_transaction(db, conf)

        split.status = "confirmed"
        db.commit()

        await bridge_client.send_message(
            split.reporter_group_jid,
            f"All shares confirmed. The ₪{float(split.total_amount):.2f} "
            f"{split.description} split has been recorded."
        )
