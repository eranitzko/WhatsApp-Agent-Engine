import json
from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry, AdminNumbers
from app.prompts.invoice_curator import INVOICE_CURATOR_SYSTEM_PROMPT
from app.prompts.notion_assistant import NOTION_ASSISTANT_SYSTEM_PROMPT
from app.prompts.family_accounting import build_family_accounting_prompt
from app.config import settings


INVOICE_CURATOR_TOOLS = [
    "get_status", "list_invoices", "get_preview", "generate_report",
    "flag_invoice", "unflag_invoice", "set_invoice_date", "set_invoice_amount",
    "add_date_format", "update_config", "request_confirmation",
]

NOTION_ASSISTANT_TOOLS = [
    "search_pages", "create_task", "append_to_page", "list_database_items",
]

FAMILY_ACCOUNTING_TOOLS = [
    "record_transaction", "record_payment", "get_balance",
    "get_history", "export_ledger", "set_reminder",
]


def _family_members() -> dict[str, str]:
    """Parse FAMILY_MEMBERS_JSON from settings. Returns empty dict on error."""
    if not settings.family_members_json:
        return {}
    try:
        return json.loads(settings.family_members_json)
    except (json.JSONDecodeError, ValueError):
        return {}


def _household_members() -> list[str]:
    """Parse FAMILY_HOUSEHOLD_MEMBERS (comma-separated names). Returns empty list on error."""
    if not settings.family_household_members:
        return []
    return [n.strip() for n in settings.family_household_members.split(",") if n.strip()]


DEFAULT_BLUEPRINTS = [
    {
        "id": "invoice_curator",
        "display_name": "Invoice Curator",
        "system_prompt": INVOICE_CURATOR_SYSTEM_PROMPT,
        "model": "claude-sonnet-4-6",
        "tools_enabled": json.dumps(INVOICE_CURATOR_TOOLS),
        "max_tool_turns": 6,
        "context_window": 8,
        "context_idle_reset_minutes": 60,
    },
    {
        "id": "notion_assistant",
        "display_name": "Notion Assistant",
        "system_prompt": NOTION_ASSISTANT_SYSTEM_PROMPT,
        "model": "claude-sonnet-4-6",
        "tools_enabled": json.dumps(NOTION_ASSISTANT_TOOLS),
        "max_tool_turns": 4,
        "context_window": 6,
        "context_idle_reset_minutes": 30,
    },
]


def seed(db: Session, admin_phone: str, legacy_group_jid: str | None = None) -> None:
    # Static blueprints
    for bp_data in DEFAULT_BLUEPRINTS:
        if not db.query(Blueprint).filter_by(id=bp_data["id"]).first():
            db.add(Blueprint(**bp_data))

    # Family accounting blueprint — system prompt built from config at seed time
    if not db.query(Blueprint).filter_by(id="family_accounting").first():
        db.add(Blueprint(
            id="family_accounting",
            display_name="Family Accounting",
            system_prompt=build_family_accounting_prompt(_family_members(), _household_members()),
            model="claude-sonnet-4-6",
            tools_enabled=json.dumps(FAMILY_ACCOUNTING_TOOLS),
            max_tool_turns=5,
            context_window=8,
            context_idle_reset_minutes=120,
        ))

    if admin_phone and not db.query(AdminNumbers).filter_by(phone_number=admin_phone).first():
        db.add(AdminNumbers(phone_number=admin_phone, label="owner"))

    if legacy_group_jid:
        if not db.query(GroupRegistry).filter_by(group_jid=legacy_group_jid).first():
            db.add(GroupRegistry(
                group_jid=legacy_group_jid,
                blueprint_id="invoice_curator",
                status="active",
                trigger_type="always",
            ))

    db.commit()
