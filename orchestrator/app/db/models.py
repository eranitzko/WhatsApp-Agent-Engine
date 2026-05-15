import json
import uuid
from datetime import datetime, timezone, date as date_type

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Index, Integer, String, Text, Numeric, ForeignKey
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


class AdminNumbers(Base):
    __tablename__ = "admin_numbers"

    phone_number = Column(String, primary_key=True)
    label = Column(String, nullable=True)
    added_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
