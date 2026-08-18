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
    AdminNumbers, CrossGroupConfirmation, GroupRegistry, Household,
    HouseholdMember, LedgerEntry, LedgerSettlement, SplitTransaction,
    UserAccount, UserProfile,
)
from app.agent.reply_words import is_affirmative, is_negative
from app.tools.accounting_fifo import DebtLeg, apply_payment, net_pair
from app.tools.accounting_tools import _net_owed
from app.utils.phone import normalize_phone

_PAYMENT_ENTRY_TYPE = "payment"
_DEBT_ENTRY_TYPE = "debt"

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
        # Check HouseholdMember display name first (most authoritative)
        member = db.query(HouseholdMember).filter_by(phone=phone).first()
        if member and member.display_name:
            return member.display_name
        # Fall back to UserProfile
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

    # ── Household resolution (replaces get_personal_group_jid guessing) ───────

    def get_household_id(self, db: Session, phone: str) -> str | None:
        """Return the household_id for a normalized phone, or None."""
        member = db.query(HouseholdMember).filter_by(phone=phone).first()
        return member.household_id if member else None

    def balance_update_message(
        self, db: Session, group_jid: str, phone: str, other_phone: str, household_id: str | None,
    ) -> str:
        """Live "Updated. Your balance with X is now: ..." notice, phone's perspective.

        Recomputed fresh from the ledger (same _net_owed helper get_balance
        uses) rather than derived from the just-applied transaction, so it
        reflects the true current state — including any bilateral-netting or
        overpayment-credit side effects of the transaction that triggered it.
        """
        other_name = self.get_display_name(db, other_phone)
        owes = _net_owed(db, group_jid, phone, other_phone, household_id)
        owed = _net_owed(db, group_jid, other_phone, phone, household_id)
        result = net_pair("you", other_name, owes - owed)
        if result is None:
            state = "settled up"
        else:
            debtor, creditor, amount = result
            # "you" is 2nd person ("you owe"), the other party is 3rd person
            # ("X owes you") — preserve both original phrasings depending on
            # which side nets debtor.
            state = f"you owe {creditor} ₪{amount:.2f}" if debtor == "you" \
                else f"{debtor} owes you ₪{amount:.2f}"
        return f"Updated. Your balance with {other_name} is now: {state}."

    def get_member_private_group(self, db: Session, phone: str) -> str | None:
        """Return the private group JID for a normalized phone via HouseholdMember.

        This is the canonical deterministic replacement for get_personal_group_jid.
        Returns None if the phone is not registered as a household member.
        """
        member = db.query(HouseholdMember).filter_by(phone=phone).first()
        return member.private_group_jid if member else None

    def get_primary_accounting_group(self, db: Session, phone: str) -> str | None:
        """Return the destination group JID for bot-initiated accounting messages.

        Priority:
          1. HouseholdMember.primary_accounting_group_jid — highest authority
             (household-enrolled users).
          2. UserProfile.primary_accounting_group_jid — override for users not
             yet enrolled in a household (covers everyone with a UserProfile).
          3. First UserAccount for this phone whose group uses the family_accounting
             blueprint, ordered by created_at ASC (deterministic; first-registered wins).

        Returns None when the person has no family_accounting group at all.
        Callers MUST surface a user-visible error — never drop silently.
        Blueprint filter in step 3 ensures invoice_curator and other-blueprint
        groups are never returned.
        """
        member = db.query(HouseholdMember).filter_by(phone=phone).first()
        if member and member.primary_accounting_group_jid:
            return member.primary_accounting_group_jid

        profile = db.query(UserProfile).filter_by(phone=phone).first()
        if profile and profile.primary_accounting_group_jid:
            return profile.primary_accounting_group_jid

        accounts = (
            db.query(UserAccount)
            .filter_by(phone=phone)
            .order_by(UserAccount.created_at.asc())
            .all()
        )
        for acct in accounts:
            reg = db.get(GroupRegistry, acct.group_jid)
            if reg and reg.blueprint_id == "family_accounting":
                return acct.group_jid

        return None

    def get_household_members(self, db: Session, household_id: str) -> list[HouseholdMember]:
        return db.query(HouseholdMember).filter_by(household_id=household_id).all()

    def resolve_inbound(
        self, db: Session, group_jid: str, raw_sender: str
    ) -> tuple[str | None, str | None]:
        """Resolve an inbound webhook (group_jid, raw_sender) to (phone, household_id).

        Strategy:
          1. Look up HouseholdMember by private_group_jid.  The group JID of a
             private 1:1 group is stable regardless of whether WhatsApp sends the
             member's E.164 or LID in the sender field.  LID-safe, but only
             covers personal 1:1 groups (private_group_jid is per-person).
          2. UserProfile by private_group_jid — same idea, covers users not yet
             household-enrolled.  Also personal-group-only.
          3. UserProfile.known_lid — a LID previously learned to belong to a
             specific person (see migration 019).  Unlike 1/2 this works in
             SHARED groups too, since it matches on the sender's own LID
             rather than on which group they're in.  Only covers LIDs that
             have actually been recorded, though.
          4. Fall back to normalize_phone on the sender JID prefix.  Works for
             phone-format senders; may return an unrecognized LID numeric for
             LID-format senders in a shared group (acceptable fallback:
             handle_confirmation_reply has a group-only fallback of its own).

        Returns (phone, household_id) where either may be None on failure.
        """
        logger.debug(
            "resolve_inbound: group_jid=%r raw_sender=%r", group_jid, raw_sender
        )

        # 1. HouseholdMember lookup — LID-safe, provides household_id
        member = db.query(HouseholdMember).filter_by(private_group_jid=group_jid).first()
        if member:
            logger.info(
                "resolve_inbound: strategy=household_member phone=%r group=%r sender=%r",
                member.phone, group_jid, raw_sender,
            )
            return member.phone, member.household_id

        # 2. UserProfile lookup — LID-safe for users not yet household-enrolled.
        #    UserProfile.private_group_jid is set automatically on personal group
        #    registration so every person who has ever registered a group is covered.
        profile = db.query(UserProfile).filter_by(private_group_jid=group_jid).first()
        if profile:
            household_id = self.get_household_id(db, profile.phone)
            logger.info(
                "resolve_inbound: strategy=user_profile phone=%r group=%r sender=%r",
                profile.phone, group_jid, raw_sender,
            )
            return profile.phone, household_id

        # 3. Extract the raw sender numeric once — used for both the known_lid
        #    lookup and (if that misses) the final unsafe fallback below.
        try:
            raw_numeric = normalize_phone(raw_sender.split("@")[0].split(":")[0])
        except ValueError:
            logger.warning("resolve_inbound: could not normalize sender %r", raw_sender)
            return None, None

        # 3a. known_lid lookup — LID-safe even in SHARED groups (unlike 1/2,
        #     which only work for personal 1:1 groups), for any LID that has
        #     actually been recorded against a person.
        lid_profile = db.query(UserProfile).filter_by(known_lid=raw_numeric).first()
        if lid_profile:
            household_id = self.get_household_id(db, lid_profile.phone)
            logger.info(
                "resolve_inbound: strategy=known_lid phone=%r group=%r sender=%r",
                lid_profile.phone, group_jid, raw_sender,
            )
            return lid_profile.phone, household_id

        # 4. Sender-JID extraction — NOT LID-safe; last resort only.
        #    Logs a warning so every fallback to this path is visible in production.
        logger.warning(
            "resolve_inbound: strategy=sender_jid (NOT LID-safe) phone=%r group=%r sender=%r",
            raw_numeric, group_jid, raw_sender,
        )
        household_id = self.get_household_id(db, raw_numeric)
        return raw_numeric, household_id

    def get_personal_group_jid(self, db: Session, phone: str) -> str | None:
        """Return the delivery group JID for a phone.

        Strategy order (most to least authoritative):
        1. HouseholdMember.private_group_jid  ← new deterministic path
        2. UserAccount (legacy)
        3. GroupParticipant exact match (legacy)
        4. AdminNumbers label fuzzy match (legacy, last resort)

        Callers should migrate to get_member_private_group() for new code.
        """
        # 1. Deterministic household path
        member_jid = self.get_member_private_group(db, phone)
        if member_jid:
            return member_jid

        # 2. UserAccount (intended path for legacy registrations)
        acct = self.resolve_user(db, phone)
        if acct:
            return acct.group_jid

        # 3. Direct GroupParticipant match
        from app.db.models import GroupParticipant
        gps = (
            db.query(GroupParticipant)
            .filter_by(phone=phone, status="active")
            .all()
        )
        fallback_jid: str | None = None
        for gp in gps:
            reg = db.get(GroupRegistry, gp.group_jid)
            if reg and reg.blueprint_id == "family_accounting":
                return gp.group_jid
            if reg and fallback_jid is None:
                fallback_jid = gp.group_jid
        if fallback_jid:
            return fallback_jid

        # 4. AdminNumbers label → push_name fuzzy match
        admin_row = db.query(AdminNumbers).filter_by(phone_number=phone).first()
        if admin_row and admin_row.label and admin_row.label.lower() != "owner":
            label_lower = admin_row.label.lower()
            all_gps = (
                db.query(GroupParticipant)
                .filter_by(status="active")
                .all()
            )
            for gp in all_gps:
                pname = (gp.admin_name or gp.push_name or "").lower()
                label_parts = label_lower.split()
                if label_parts and any(part in pname for part in label_parts):
                    reg = db.get(GroupRegistry, gp.group_jid)
                    if reg and reg.blueprint_id == "family_accounting":
                        return gp.group_jid

        return None

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

    # ── Ledger scope helpers ──────────────────────────────────────────────────

    def get_ledger_scope(
        self, db: Session, phone: str, group_jid: str
    ) -> tuple[str | None, str]:
        """Return (household_id_or_None, group_jid) for use in ledger queries.

        When household_id is not None, callers should filter by household_id.
        When None, callers fall back to group_jid (legacy data).
        """
        return self.get_household_id(db, phone), group_jid

    # ── Cross-group notifications ─────────────────────────────────────────────

    async def notify_user(self, db: Session, target_phone: str, message: str) -> None:
        target_jid = self.get_primary_accounting_group(db, target_phone)
        if not target_jid:
            logger.warning("notify_user: no family_accounting group for %s", target_phone)
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
    ) -> tuple[CrossGroupConfirmation, bool]:
        """Create a CrossGroupConfirmation and attempt to deliver it to the target.

        Returns (conf, delivered). The row is always committed, whether or not
        delivery succeeds — a transient bridge failure must not force the
        reporter to redo the whole entry from scratch. When delivered is False,
        the row is still saved as an ordinary pending confirmation so
        resend_confirmation can retry it later; the caller should tell the
        reporter honestly that it's recorded but not yet delivered, rather than
        claiming either full success or total loss.

        Raises ValueError if the target has no accounting group at all — that's
        a permanent setup problem, not a transient delivery failure, so there's
        nothing to save or retry.
        """
        target_jid = self.get_primary_accounting_group(db, target_phone)
        if not target_jid:
            raise ValueError(
                f"{target_phone} is not set up for shared accounting — "
                f"they need to register a family_accounting group first."
            )

        # Resolve household_id for LID-safe matching (may be None for legacy setups)
        initiator_household_id = self.get_household_id(db, initiator_phone)
        target_household_id = self.get_household_id(db, target_phone)
        household_id = initiator_household_id or target_household_id  # prefer whichever is set

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
            household_id=household_id,
        )
        db.add(conf)
        db.flush()

        delivered = True
        try:
            await bridge_client.send_message(target_jid, confirmation_message)
        except Exception:
            logger.exception(
                "request_confirmation: failed to deliver to %s (%s) — keeping "
                "as pending for resend_confirmation",
                target_phone, target_jid,
            )
            delivered = False

        db.commit()
        db.refresh(conf)
        return conf, delivered

    def handle_confirmation_reply(
        self,
        db: Session,
        group_jid: str,
        phone: str,
        reply: str,
        household_id: str | None = None,
    ) -> "CrossGroupConfirmation | None":
        """Update and return the resolved confirmation, or None if none found.

        Match priority:
        1. (household_id, phone, status=pending) — LID-safe, used when household
           is configured.
        2. (phone, group_jid, status=pending) — exact phone+group match.
        3. (group_jid, status=pending) group-only fallback — safe only when
           exactly one pending conf exists for this group.

        Returns the CrossGroupConfirmation with updated status so callers can act
        on it directly without a second DB query (which would fail on LID mismatch).
        """
        now = datetime.now(timezone.utc)

        conf: CrossGroupConfirmation | None = None

        # 1. Household-based match (LID-safe)
        if household_id:
            pending = (
                db.query(CrossGroupConfirmation)
                .filter_by(household_id=household_id, target_phone=phone, status="pending")
                .filter(CrossGroupConfirmation.expires_at > now)
                .order_by(CrossGroupConfirmation.created_at.asc())
                .all()
            )
            if len(pending) == 1:
                conf = pending[0]
            elif len(pending) > 1:
                # Multiple pending for this person — cannot auto-resolve; caller
                # must present them sequentially.  Return first (oldest) for now.
                conf = pending[0]
                logger.info(
                    "handle_confirmation_reply: %d pending confs for household=%s phone=%s, "
                    "resolving oldest first (%s)",
                    len(pending), household_id, phone, conf.id,
                )

        # 2. Exact phone + group match
        if conf is None:
            conf = (
                db.query(CrossGroupConfirmation)
                .filter_by(target_phone=phone, target_group_jid=group_jid, status="pending")
                .filter(CrossGroupConfirmation.expires_at > now)
                .order_by(CrossGroupConfirmation.created_at.asc())
                .first()
            )

        # 3. Group-only fallback (handles LID ↔ phone mismatch when not on household model)
        if conf is None:
            pending_for_group = (
                db.query(CrossGroupConfirmation)
                .filter_by(target_group_jid=group_jid, status="pending")
                .filter(CrossGroupConfirmation.expires_at > now)
                .all()
            )
            if len(pending_for_group) == 1:
                conf = pending_for_group[0]

        if conf is None:
            return None

        if is_affirmative(reply):
            conf.status = "confirmed"
        elif is_negative(reply):
            conf.status = "rejected"
        else:
            return None

        db.commit()
        return conf

    # ── Transaction processing ────────────────────────────────────────────────

    def _is_first_party(self, reporter_phone: str, other_phone: str) -> bool:
        """True when reporter and other_phone are the same normalized phone."""
        return normalize_phone(reporter_phone) == normalize_phone(other_phone)

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
        reporter_phone = normalize_phone(reporter_phone)
        payer_phone = normalize_phone(payer_phone)
        debtor_phone = normalize_phone(debtor_phone)

        payer_name = self.get_display_name(db, payer_phone)
        debtor_name = self.get_display_name(db, debtor_phone)

        household_id = self.get_household_id(db, reporter_phone)

        if self._is_first_party(reporter_phone, debtor_phone):
            # "I owe C" — debtor self-reporting → works AGAINST reporter → RECORD immediately
            entry = LedgerEntry(
                transaction_id=str(_uuid_mod.uuid4()),
                entry_type=_DEBT_ENTRY_TYPE,
                household_id=household_id,
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
                f"({description}). "
                + self.balance_update_message(db, reporter_group_jid, payer_phone, debtor_phone, household_id)
            )
            await self.notify_user(db, payer_phone, notify_msg)
            return f"Recorded. {payer_name} has been notified."
        else:
            # "C owes me" — reporter asserting debtor's obligation → works IN FAVOR → CONFIRM
            confirm_msg = (
                f"{payer_name} says you owe ₪{float(amount_ils):.2f} ({description}). "
                f"Confirm? (yes / no)"
            )
            try:
                _conf, delivered = await self.request_confirmation(
                    db=db,
                    initiator_phone=reporter_phone,
                    initiator_group_jid=reporter_group_jid,
                    target_phone=debtor_phone,
                    action_type="record_expense",
                    action_payload={
                        "group_jid": reporter_group_jid,
                        "household_id": household_id,
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
            except ValueError as exc:
                return str(exc)
            if delivered:
                return f"Confirmation request sent to {debtor_name}. I'll notify you when they respond."
            return (
                f"Recorded — but I couldn't reach {debtor_name} directly to ask for confirmation. "
                f"It's saved as pending; ask them to check in, or try resend_confirmation later."
            )

    # ── Payment FIFO settlement ───────────────────────────────────────────────

    def _open_debt_legs(
        self,
        db: Session,
        group_jid: str,
        from_phone: str,
        to_phone: str,
        household_id: str | None,
    ) -> list[DebtLeg]:
        from app.tools.accounting_fifo import fetch_open_debt_legs
        return fetch_open_debt_legs(db, group_jid, from_phone, to_phone, household_id)

    async def _apply_payment_fifo(
        self,
        db: Session,
        group_jid: str,
        payer_phone: str,
        payee_phone: str,
        amount_ils: Decimal,
        payment_date: _date,
        household_id: str | None = None,
    ) -> str:
        """Apply FIFO settlement, scoped by household_id when available.

        A payment can exceed what the payer owes the payee in that direction.
        Rather than silently dropping the excess (the original bug — real ILS
        amounts vanishing with no ledger trace), this bilaterally nets: any
        leftover is applied FIFO against the reverse-direction debt (payee
        owes payer) too. If leftover still remains after that — the payer has
        paid beyond every known obligation either way — it's recorded as a
        fresh open reverse-direction debt ("credit from overpayment") so it
        automatically offsets the payee's next debt to the payer instead of
        disappearing.
        """
        now = datetime.now(timezone.utc)
        all_settlements: list[tuple[str, Decimal]] = []

        same_dir_legs = self._open_debt_legs(db, group_jid, payer_phone, payee_phone, household_id)
        result = apply_payment(amount_ils, same_dir_legs)
        for leg_id, new_settled in result.updated_legs:
            row = db.get(LedgerEntry, leg_id)
            if row:
                row.amount_settled_ils = new_settled
        all_settlements.extend(result.settlements)
        remaining = result.leftover

        if remaining > Decimal("0"):
            reverse_legs = self._open_debt_legs(db, group_jid, payee_phone, payer_phone, household_id)
            result2 = apply_payment(remaining, reverse_legs)
            for leg_id, new_settled in result2.updated_legs:
                row = db.get(LedgerEntry, leg_id)
                if row:
                    row.amount_settled_ils = new_settled
            all_settlements.extend(result2.settlements)
            remaining = result2.leftover

        payment_leg = LedgerEntry(
            transaction_id=str(_uuid_mod.uuid4()),
            entry_type=_PAYMENT_ENTRY_TYPE,
            household_id=household_id,
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
        for debt_leg_id, applied_amount in all_settlements:
            db.add(LedgerSettlement(
                payment_leg_id=payment_leg.id,
                debt_leg_id=debt_leg_id,
                amount_ils=applied_amount,
                created_at=now,
            ))

        if remaining > Decimal("0"):
            db.add(LedgerEntry(
                transaction_id=str(_uuid_mod.uuid4()),
                entry_type=_DEBT_ENTRY_TYPE,
                household_id=household_id,
                group_jid=group_jid,
                from_phone=payee_phone,
                to_phone=payer_phone,
                amount_ils=remaining,
                amount_settled_ils=Decimal("0"),
                description=f"Credit from overpayment on {payment_date.isoformat()}",
                transaction_date=payment_date,
            ))

        db.commit()
        parts = [f"{amt:.2f} ILS off {did[:8]}" for did, amt in all_settlements]
        summary = "; ".join(parts) if parts else "no open debts found"
        if remaining > Decimal("0"):
            summary += f"; {remaining:.2f} ILS recorded as credit (overpayment)"
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
        """Route a payment through the correct confirm/record path.

        Canonical rule (works AGAINST reporter's interest → RECORD):
          "C paid me" (reporter == payee): payee records receipt → reduces what C
          owes them → RECORD immediately + notify the payer C.

          "I paid C" (reporter == payer): payer asserts they paid → reduces their
          own debt → works IN FAVOR of reporter → payee C must CONFIRM receipt.
        """
        reporter_phone = normalize_phone(reporter_phone)
        payer_phone = normalize_phone(payer_phone)
        payee_phone = normalize_phone(payee_phone)

        payer_name = self.get_display_name(db, payer_phone)
        payee_name = self.get_display_name(db, payee_phone)

        household_id = self.get_household_id(db, reporter_phone)

        if reporter_phone == payee_phone:
            # "C paid me" — payee self-reports receiving payment
            # Works AGAINST reporter (reduces what they're owed) → RECORD immediately
            summary = await self._apply_payment_fifo(
                db, reporter_group_jid, payer_phone, payee_phone,
                amount_ils, payment_date, household_id=household_id,
            )
            notify_msg = (
                f"{payee_name} confirmed your ₪{float(amount_ils):.2f} payment. "
                + self.balance_update_message(db, reporter_group_jid, payer_phone, payee_phone, household_id)
            )
            await self.notify_user(db, payer_phone, notify_msg)
            return f"Recorded. {payer_name} has been notified."
        else:
            # "I paid C" — payer self-reports making a payment
            # Works IN FAVOR of reporter (reduces own debt) → payee must CONFIRM receipt
            confirm_msg = (
                f"{payer_name} says they paid you ₪{float(amount_ils):.2f} on {payment_date}. "
                f"Confirm? (yes / no)"
            )
            try:
                await self.request_confirmation(
                    db=db,
                    initiator_phone=reporter_phone,
                    initiator_group_jid=reporter_group_jid,
                    target_phone=payee_phone,
                    action_type="record_payment",
                    action_payload={
                        "group_jid": reporter_group_jid,
                        "household_id": household_id,
                        "payer_phone": payer_phone,
                        "payee_phone": payee_phone,
                        "amount_ils": str(amount_ils),
                        "payment_date": str(payment_date),
                    },
                    confirmation_message=confirm_msg,
                )
            except (ValueError, RuntimeError) as exc:
                return str(exc)
            return f"Confirmation request sent to {payee_name}. I'll notify you when they respond."

    async def commit_confirmed_transaction(self, db: Session, conf: CrossGroupConfirmation) -> None:
        """Write the ledger entry (or payment) for a confirmed 2nd-party transaction.

        Atomicity: the LedgerEntry write and the conf status are updated in the
        same DB commit.  The conf status was already set to 'confirmed' by
        handle_confirmation_reply before this is called, so here we only write
        the ledger entry and send notifications.
        """
        payload = json.loads(conf.action_payload)
        household_id = payload.get("household_id") or conf.household_id

        if conf.action_type == "record_payment":
            # Payee confirmed receiving payer's payment ("I paid C" flow)
            payment_date = _date.fromisoformat(payload["payment_date"])
            amount_ils = Decimal(payload["amount_ils"])
            await self._apply_payment_fifo(
                db,
                payload["group_jid"],
                payload["payer_phone"],
                payload["payee_phone"],
                amount_ils,
                payment_date,
                household_id=household_id,
            )
            payer_name = self.get_display_name(db, payload["payer_phone"])
            payee_name = self.get_display_name(db, payload["payee_phone"])
            # Notify the payer (initiator) that payee confirmed receipt
            await self.notify_user(
                db, payload["payer_phone"],
                f"{payee_name} confirmed your ₪{float(amount_ils):.2f} payment. "
                + self.balance_update_message(
                    db, payload["group_jid"], payload["payer_phone"], payload["payee_phone"], household_id
                )
            )
            # Acknowledge to the payee (target who just confirmed)
            try:
                await bridge_client.send_message(
                    conf.target_group_jid,
                    f"Confirmed. {payer_name}'s payment of ₪{float(amount_ils):.2f} has been recorded."
                )
            except Exception:
                logger.exception("commit_confirmed_transaction: failed to ack payee %s", conf.target_group_jid)
            return

        # Default: record_expense — "C owes me" confirmed by C
        entry = LedgerEntry(
            transaction_id=str(_uuid_mod.uuid4()),
            entry_type=_DEBT_ENTRY_TYPE,
            household_id=household_id,
            group_jid=payload["group_jid"],
            from_phone=payload["debtor_phone"],
            to_phone=payload["payer_phone"],
            amount_ils=Decimal(payload["amount_ils"]),
            description=payload["description"],
            transaction_date=_date.fromisoformat(payload["transaction_date"]),
        )
        db.add(entry)
        db.commit()

        debtor_name = self.get_display_name(db, payload["debtor_phone"])
        payer_name = self.get_display_name(db, payload["payer_phone"])
        # Notify the creditor (initiator/payer) that debtor confirmed
        await self.notify_user(
            db, payload["payer_phone"],
            f"{debtor_name} confirmed the ₪{float(entry.amount_ils):.2f} debt "
            f"({payload['description']}). "
            + self.balance_update_message(
                db, payload["group_jid"], payload["payer_phone"], payload["debtor_phone"], household_id
            )
        )
        # Acknowledge to the debtor (target who just confirmed)
        try:
            await bridge_client.send_message(
                conf.target_group_jid,
                f"Confirmed. Your debt of ₪{float(entry.amount_ils):.2f} to {payer_name} "
                f"has been recorded."
            )
        except Exception:
            logger.exception("commit_confirmed_transaction: failed to ack debtor %s", conf.target_group_jid)

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
        reporter_phone = normalize_phone(reporter_phone)
        payer_phone = normalize_phone(payer_phone)

        household_id = self.get_household_id(db, reporter_phone)

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
            phone = normalize_phone(share["phone"])
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
                        "household_id": household_id,
                        "payer_phone": payer_phone,
                        "debtor_phone": phone,
                        "amount_ils": str(amount),
                        "description": description,
                        "transaction_date": str(transaction_date),
                        "split_transaction_id": split.id,
                    }),
                    status="self_confirmed",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=self._confirmation_timeout_hours(db)),
                    household_id=household_id,
                )
                db.add(conf)
            else:
                debtor_name = self.get_display_name(db, phone)
                confirm_msg = (
                    f"{debtor_name}, your share of a ₪{float(total_amount):.2f} "
                    f"{description} with {payer_name} is ₪{float(amount):.2f}. "
                    f"Confirm? (yes / no)"
                )
                try:
                    _conf, delivered = await self.request_confirmation(
                        db=db,
                        initiator_phone=reporter_phone,
                        initiator_group_jid=reporter_group_jid,
                        target_phone=phone,
                        action_type="split_share",
                        action_payload={
                            "group_jid": reporter_group_jid,
                            "household_id": household_id,
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
                except ValueError as exc:
                    # Target has no accounting group at all — nothing to save or retry.
                    logger.warning(
                        "process_split: %s has no accounting group for split %s: %s",
                        phone, split.id, exc,
                    )
                    continue
                if not delivered:
                    # Share is still recorded as a pending confirmation (see
                    # request_confirmation) — just couldn't notify them right now.
                    logger.warning(
                        "process_split: could not reach %s for split %s — "
                        "kept as pending for resend_confirmation",
                        phone, split.id,
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

        await bridge_client.send_message(
            split.reporter_group_jid,
            f"{decliner_name} declined their share of the ₪{float(split.total_amount):.2f} "
            f"{split.description}. The split is suspended — re-submit if you agree on new amounts."
        )
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

    # ── Household management ──────────────────────────────────────────────────

    def create_household(self, db: Session, name: str) -> Household:
        household = Household(name=name)
        db.add(household)
        db.commit()
        db.refresh(household)
        return household

    def add_household_member(
        self,
        db: Session,
        household_id: str,
        phone: str,
        private_group_jid: str | None = None,
        display_name: str | None = None,
    ) -> HouseholdMember:
        phone = normalize_phone(phone)
        existing = db.query(HouseholdMember).filter_by(phone=phone).first()
        if existing:
            if existing.household_id != household_id:
                raise ValueError(
                    f"Phone {phone} is already a member of household {existing.household_id}"
                )
            # Update fields if provided
            if private_group_jid is not None:
                existing.private_group_jid = private_group_jid
            if display_name is not None:
                existing.display_name = display_name
            db.commit()
            return existing

        member = HouseholdMember(
            household_id=household_id,
            phone=phone,
            private_group_jid=private_group_jid,
            display_name=display_name,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        return member

    def remove_household_member(self, db: Session, household_id: str, phone: str) -> bool:
        phone = normalize_phone(phone)
        row = db.query(HouseholdMember).filter_by(
            household_id=household_id, phone=phone
        ).first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True

    def list_households(self, db: Session) -> list[Household]:
        return db.query(Household).order_by(Household.name).all()
