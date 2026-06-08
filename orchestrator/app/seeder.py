import json
from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry, AdminNumbers
from app.prompts.invoice_curator import INVOICE_CURATOR_SYSTEM_PROMPT
from app.prompts.notion_assistant import NOTION_ASSISTANT_SYSTEM_PROMPT
from app.prompts.family_accounting import FAMILY_ACCOUNTING_SYSTEM_PROMPT


AUTOMATION_TOOLS = [
    "create_automation", "activate_automation", "list_automations",
    "pause_automation", "cancel_automation",
    "export_invoice_report", "export_accounting_report",
    "send_email",
]

INVOICE_CURATOR_TOOLS = [
    "get_status", "list_invoices", "get_invoice_summary",
    "flag_invoice", "unflag_invoice", "set_invoice_date", "set_invoice_amount",
    "add_date_format", "update_config", "stage_action",
    *AUTOMATION_TOOLS,
]

NOTION_ASSISTANT_TOOLS = [
    "search_pages", "create_task", "append_to_page", "list_database_items",
]

FAMILY_ACCOUNTING_TOOLS = [
    "record_expense", "record_payment", "get_balance",
    "get_history", "set_reminder", "list_reminders", "cancel_reminder",
    "set_report_email", "rename_participant", "set_household", "list_participants",
    "correct_transaction", "commit_correction",
    "create_report_format", "list_report_formats", "delete_report_format",
    *AUTOMATION_TOOLS,
]


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


def _merge_tools(existing_json: str, canonical: list[str]) -> str:
    """Add any tools from canonical that are missing from existing, preserving order and extras."""
    existing = json.loads(existing_json or "[]")
    existing_set = set(existing)
    merged = existing + [t for t in canonical if t not in existing_set]
    return json.dumps(merged)


def seed(db: Session, admin_phone: str, legacy_group_jid: str | None = None) -> None:
    # Static blueprints — upsert on each startup so DB stays in sync
    for bp_data in DEFAULT_BLUEPRINTS:
        existing = db.query(Blueprint).filter_by(id=bp_data["id"]).first()
        if existing:
            existing.model = bp_data["model"]
            # Merge: add new canonical tools without removing admin-added ones
            existing.tools_enabled = _merge_tools(
                existing.tools_enabled, json.loads(bp_data["tools_enabled"])
            )
        else:
            db.add(Blueprint(**bp_data))

    # Family accounting blueprint — upsert static prompt so old template-based
    # rows are replaced on next startup.
    fa_bp = db.query(Blueprint).filter_by(id="family_accounting").first()
    if fa_bp:
        fa_bp.system_prompt = FAMILY_ACCOUNTING_SYSTEM_PROMPT
        fa_bp.model = "claude-sonnet-4-6"
        # Merge: add new canonical tools without removing admin-added ones
        fa_bp.tools_enabled = _merge_tools(fa_bp.tools_enabled, FAMILY_ACCOUNTING_TOOLS)
    else:
        db.add(Blueprint(
            id="family_accounting",
            display_name="Family Accounting",
            system_prompt=FAMILY_ACCOUNTING_SYSTEM_PROMPT,
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
