import json
import uuid
from datetime import datetime, timezone, date as date_type
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Index, Integer, String, Text, Numeric, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(String(36), primary_key=True, default=_uuid)
    group_id = Column(String(255), nullable=False, index=True)
    message_id = Column(String(255), nullable=False, unique=True)
    image_hash = Column(String(64), nullable=False, index=True)   # SHA-256 hex
    r2_key = Column(String(512), nullable=True)                    # R2 object key for resized image

    received_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    invoice_date = Column(Date, nullable=True)
    invoice_number = Column(String(255), nullable=True)
    vendor = Column(String(512), nullable=True)
    description = Column(Text, nullable=True)

    amount_original = Column(Numeric(18, 4), nullable=True)
    currency_original = Column(String(3), nullable=True)           # ISO 4217
    amount_ils = Column(Numeric(18, 4), nullable=True)             # Converted to ILS
    exchange_rate = Column(Numeric(18, 6), nullable=True)          # 1 unit original = X ILS
    rate_source = Column(String(10), nullable=True)                # "boi", "yfinance", "none"
    rate_date = Column(Date, nullable=True)

    extraction_confidence = Column(Float, nullable=True)
    flagged = Column(Boolean, nullable=False, default=False)
    flag_reason = Column(Text, nullable=True)

    submitted_by = Column(String(255), nullable=True)              # WA JID of sender
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_invoices_group_date", "group_id", "invoice_date"),
        Index("ix_invoices_group_hash", "group_id", "image_hash"),
    )


class GroupConfig(Base):
    __tablename__ = "group_config"

    group_id = Column(String(255), primary_key=True)
    report_header = Column(String(512), nullable=False, default="Monthly Invoice Report")
    report_author = Column(String(255), nullable=False, default="")
    feedback_language = Column(String(2), nullable=False, default="en")  # "en" or "he"
    lead_currency = Column(String(3), nullable=False, default="ILS")
    force_dual_currency = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ExchangeRateCache(Base):
    __tablename__ = "exchange_rate_cache"

    id = Column(String(36), primary_key=True, default=_uuid)
    currency_from = Column(String(3), nullable=False)
    currency_to = Column(String(3), nullable=False)
    rate = Column(Numeric(18, 6), nullable=False)      # 1 unit from = X to
    rate_date = Column(Date, nullable=False)
    source = Column(String(10), nullable=False)         # "boi" or "yfinance"
    fetched_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class SystemConfig(Base):
    __tablename__ = "system_config"

    key   = Column(String(255), primary_key=True)
    value = Column(Text, nullable=False, default="")


class ConversationHistory(Base):
    __tablename__ = "conversation_history"

    group_id       = Column(String(255), primary_key=True)
    messages_json  = Column(Text, nullable=False, default="[]")  # JSON array of {role, content}
    last_active    = Column(DateTime(timezone=True), nullable=False,
                            default=lambda: datetime.now(timezone.utc))


class Blueprint(Base):
    __tablename__ = "blueprints"

    id = Column(String, primary_key=True)
    display_name = Column(String, nullable=False)
    system_prompt = Column(Text, nullable=False)
    model = Column(String, nullable=False, default="claude-sonnet-4-6")
    tools_enabled = Column(Text, nullable=False)  # JSON array string
    max_tool_turns = Column(Integer, default=6)
    context_window = Column(Integer, default=8)
    context_idle_reset_minutes = Column(Integer, default=60)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def tools_list(self) -> list[str]:
        return json.loads(self.tools_enabled)


class GroupRegistry(Base):
    __tablename__ = "group_registry"

    group_jid = Column(String, primary_key=True)
    blueprint_id = Column(String, ForeignKey("blueprints.id"), nullable=False)
    status = Column(String, nullable=False, default="active")       # active | paused
    trigger_type = Column(String, nullable=False, default="always")  # always | mention | prefix
    trigger_prefix = Column(String, nullable=True)
    bound_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    custom_instructions = Column(Text, nullable=True)
    group_type = Column(String, nullable=True, default="personal")  # personal|shared|sys_admin|unregistered


class GroupParticipant(Base):
    __tablename__ = "group_participants"

    group_jid  = Column(String, ForeignKey("group_registry.group_jid"), primary_key=True)
    phone      = Column(String, primary_key=True)
    push_name  = Column(String, nullable=True)
    admin_name = Column(String, nullable=True)
    is_household = Column(Boolean, nullable=False, default=False)
    status     = Column(String, nullable=False, default="active")   # active | removed
    joined_at  = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    removed_at = Column(DateTime(timezone=True), nullable=True)


class AdminNumbers(Base):
    __tablename__ = "admin_numbers"

    phone_number = Column(String, primary_key=True)
    label = Column(String, nullable=True)
    added_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id                 = Column(String(36), primary_key=True, default=_uuid)
    transaction_id     = Column(String(36), nullable=False, index=True)
    # household_id is the canonical query scope for bilateral ledger entries.
    # Null for legacy rows written before migration 015; those fall back to group_jid.
    household_id       = Column(String(36), ForeignKey("households.id"), nullable=True, index=True)
    # group_jid is kept for backward-compat and as origin audit metadata.
    # New code: treat as origin_group_jid (which group triggered this entry).
    group_jid          = Column(String(255), nullable=False, index=True)
    from_phone         = Column(String(255), nullable=False)
    to_phone           = Column(String(255), nullable=False)
    entry_type         = Column(String(16), nullable=False, default="debt")  # 'debt' | 'payment'
    amount_ils         = Column(Numeric(18, 4), nullable=False)
    amount_settled_ils = Column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    description        = Column(Text, nullable=False, default="")
    transaction_date   = Column(Date, nullable=False)
    created_at         = Column(DateTime(timezone=True), nullable=False,
                                default=lambda: datetime.now(timezone.utc))

    @property
    def remaining_ils(self) -> Decimal:
        return self.amount_ils - (self.amount_settled_ils or Decimal("0"))


class LedgerSettlement(Base):
    __tablename__ = "ledger_settlements"

    id             = Column(String(36), primary_key=True, default=_uuid)
    payment_leg_id = Column(String(36), ForeignKey("ledger_entries.id"), nullable=False)
    debt_leg_id    = Column(String(36), ForeignKey("ledger_entries.id"), nullable=False)
    amount_ils     = Column(Numeric(18, 4), nullable=False)
    created_at     = Column(DateTime(timezone=True), nullable=False,
                            default=lambda: datetime.now(timezone.utc))


class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"

    id         = Column(String(36), primary_key=True, default=_uuid)
    group_jid  = Column(String(255), nullable=False)
    to_phone   = Column(String(255), nullable=False)
    message    = Column(Text, nullable=False)
    send_at    = Column(DateTime(timezone=True), nullable=False)
    sent       = Column(Boolean, nullable=False, default=False)
    cancelled  = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id               = Column(String(36), primary_key=True, default=_uuid)
    group_jid        = Column(String, ForeignKey("group_registry.group_jid"), nullable=False, index=True)
    name             = Column(String, nullable=False)
    rule_type        = Column(String, nullable=False)   # one_off|recurring|inactivity|threshold|event_trigger
    schedule_cron    = Column(String, nullable=True)    # ISO datetime str for one_off; cron expr for recurring
    inactivity_hours = Column(Integer, nullable=True)
    threshold_config = Column(Text, nullable=True)      # JSON: {"metric": str, "op": str, "value": float}
    action_type      = Column(String, nullable=False)   # send_message|run_agent_action
    action_config    = Column(Text, nullable=False)     # JSON: {"message": str} or {"action": str}
    status           = Column(String, nullable=False, default="pending_confirm")  # pending_confirm|active|paused|done
    last_fired_at    = Column(DateTime(timezone=True), nullable=True)
    created_at       = Column(DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(timezone.utc))


class RequestLog(Base):
    __tablename__ = "request_logs"

    id              = Column(String(36), primary_key=True, default=_uuid)
    group_jid       = Column(String, nullable=False, index=True)
    blueprint_id    = Column(String, nullable=False)
    sender_phone    = Column(String, nullable=True)
    history_pairs   = Column(Integer, nullable=False, default=0)
    tool_count      = Column(Integer, nullable=False, default=0)
    tool_names      = Column(Text, nullable=True)       # JSON array
    stop_reason     = Column(String, nullable=True)
    tool_calls_made = Column(Text, nullable=True)       # JSON array of {name, preview}
    error           = Column(Text, nullable=True)
    duration_ms     = Column(Integer, nullable=True)
    created_at      = Column(DateTime(timezone=True), nullable=False,
                             default=lambda: datetime.now(timezone.utc))


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id         = Column(String(36), primary_key=True, default=_uuid)
    phone      = Column(String, nullable=False, index=True)
    group_jid  = Column(String, ForeignKey("group_registry.group_jid"), nullable=False, index=True)
    role       = Column(String, nullable=False, default="owner")   # owner | member
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("phone", "group_jid", name="uq_user_accounts_phone_group"),
    )


class SplitTransaction(Base):
    __tablename__ = "split_transactions"

    id                 = Column(String(36), primary_key=True, default=_uuid)
    reporter_group_jid = Column(String, nullable=False)
    reporter_phone     = Column(String, nullable=False)
    payer_phone        = Column(String, nullable=False)
    total_amount       = Column(Numeric(18, 4), nullable=False)
    description        = Column(Text, nullable=True)
    status             = Column(String, nullable=False, default="pending")  # pending|confirmed|suspended|cancelled
    created_at         = Column(DateTime(timezone=True), nullable=False,
                                default=lambda: datetime.now(timezone.utc))


class CrossGroupConfirmation(Base):
    __tablename__ = "cross_group_confirmations"

    id                   = Column(String(36), primary_key=True, default=_uuid)
    split_transaction_id = Column(String(36), ForeignKey("split_transactions.id", ondelete="CASCADE"), nullable=True)
    initiator_phone      = Column(String, nullable=False)
    initiator_group_jid  = Column(String, nullable=False)
    target_phone         = Column(String, nullable=False)
    target_group_jid     = Column(String, nullable=False)
    action_type          = Column(String, nullable=False)   # record_expense|record_payment|split_share
    action_payload       = Column(Text, nullable=False)     # JSON
    status               = Column(String, nullable=False, default="pending")  # pending|confirmed|rejected|timed_out
    expires_at           = Column(DateTime(timezone=True), nullable=False)
    created_at           = Column(DateTime(timezone=True), nullable=False,
                                  default=lambda: datetime.now(timezone.utc))
    # household_id enables matching by (household_id, target_phone) rather than group_jid,
    # which survives LID/phone mismatches and group-registration gaps.
    # Null for legacy rows; populated for all new confirmations after migration 015.
    household_id         = Column(String(36), ForeignKey("households.id"), nullable=True)
    # Re-send tracking: initiator may re-send the confirmation message to the target,
    # subject to rate limits (max 2 per 24h, at least 2h apart).
    resend_count         = Column(Integer, nullable=False, default=0, server_default="0")
    last_resent_at       = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_cross_group_confirmations_target_phone_status", "target_phone", "status"),
        Index("ix_cross_group_confirmations_household_target", "household_id", "target_phone", "status"),
    )


class Household(Base):
    __tablename__ = "households"

    id         = Column(String(36), primary_key=True, default=_uuid)
    name       = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))


class HouseholdMember(Base):
    """Maps a normalized phone → household, with the person's private group JID.

    phone must be the canonical normalized form (digits only, 7–18 chars).
    private_group_jid is the WhatsApp group that serves as this person's inbox
    (one human + the bot).  This replaces the unreliable get_personal_group_jid
    three-strategy fallback.
    """
    __tablename__ = "household_members"

    id                         = Column(String(36), primary_key=True, default=_uuid)
    household_id               = Column(String(36), ForeignKey("households.id"), nullable=False, index=True)
    phone                      = Column(String, nullable=False)
    private_group_jid          = Column(String, ForeignKey("group_registry.group_jid"), nullable=True)
    primary_accounting_group_jid = Column(String, ForeignKey("group_registry.group_jid"), nullable=True)
    display_name               = Column(String, nullable=True)
    created_at                 = Column(DateTime(timezone=True), nullable=False,
                                        default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("household_id", "phone", name="uq_household_members_household_phone"),
        UniqueConstraint("phone", name="uq_household_members_phone"),  # one household per phone
        Index("ix_household_members_phone", "phone"),
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    phone                        = Column(String, primary_key=True)
    email                        = Column(String, nullable=True)
    display_name                 = Column(String, nullable=True)
    # Routing fields — populated automatically on personal group registration.
    # These exist on UserProfile (not only HouseholdMember) so that LID-safe
    # inbound resolution and primary-group overrides work for every person who
    # has registered a group, regardless of household enrollment status.
    private_group_jid            = Column(String, ForeignKey("group_registry.group_jid"), nullable=True, index=True)
    primary_accounting_group_jid = Column(String, ForeignKey("group_registry.group_jid"), nullable=True)
    created_at                   = Column(DateTime(timezone=True), nullable=False,
                                          default=lambda: datetime.now(timezone.utc))


class EmailAllowlist(Base):
    __tablename__ = "email_allowlist"

    email        = Column(String, primary_key=True)
    display_name = Column(String, nullable=True)
    created_at   = Column(DateTime(timezone=True), nullable=False,
                          server_default=sa.func.now(),
                          default=lambda: datetime.now(timezone.utc))


class ReportFormat(Base):
    __tablename__ = "report_formats"

    id         = Column(String(36), primary_key=True, default=_uuid)
    group_jid  = Column(String, nullable=False)
    name       = Column(String, nullable=False)
    config_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))

    def config(self) -> dict:
        return json.loads(self.config_json)
