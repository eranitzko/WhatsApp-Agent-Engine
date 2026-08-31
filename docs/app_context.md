# WhatsApp Agent Engine — Machine Context

## STACK
- Bridge: Node.js WebSocket (whatsmeow), port 3001 internal
- Orchestrator: FastAPI (async), port 8080 external, Python 3.12
- DB: SQLite at /data/invoice_curator.db via SQLAlchemy + Alembic
- AI: AsyncAnthropic (claude-sonnet-4-6, max_retries=4)
- Scheduler: APScheduler (async)
- Email: Gmail SMTP (gmail_user / gmail_app_password)
- Storage: Cloudflare R2 (invoice images)
- OCR: Gemini 1.5 Pro (image→invoice extraction)

## REPO LAYOUT
orchestrator/app/
  main.py               FastAPI app, lifespan, webhook handler
  agent_runner.py       Core agent loop + confirmation intercept
  tool_registry.py      ToolRegistry: register/get_schemas/execute
  router.py             Blueprint routing (TTL=300s cache)
  scheduler.py          APScheduler jobs
  seeder.py             DB init (blueprints, admin numbers)
  command_handler.py    /bind and similar WhatsApp commands
  participants.py       Participant block builder for system prompt
  bridge_client.py      HTTP client to bridge
  registry_ref.py       Global ToolRegistry singleton ref
  config.py             Pydantic settings from .env
  agent/
    context.py          ContextStore + GroupContext (SQLite-persisted history)
    confirmation.py     Single-action ConfirmationStore (in-memory, 5m TTL)
    multi_confirmation.py  Multi-party confirmations (DB, 5m TTL)
    tools.py            Invoice tool schemas + executors (legacy, partly superseded)
    correction_queue.py Transaction correction queue
  tools/
    invoice_tools.py    Invoice CRUD + confirmed executors (remove_invoice, send_email)
    accounting_tools.py Ledger, payment, correction, export tools (~1200 lines)
    accounting_fx.py    FX conversion → ILS
    accounting_fifo.py  FIFO settlement logic
    split_tools.py      record_split
    automation_tools.py Automation CRUD
    send_email_tool.py  Custom email with template vars
    notion_tools.py     Optional Notion integration
  accounting/
    account_service.py  AccountService: user/group resolution, FIFO settlement
    group_registration.py  Bot join handler
  automation/
    executor.py         AutomationExecutor (workflows, templates, metrics)
    evaluator.py        Threshold/condition evaluation
  export/
    tool.py             export_report orchestrator
    delivery.py         File delivery to group or email
    generators/
      invoice.py        InvoiceGenerator PDF/XLSX
      accounting.py     AccountingGenerator PDF/XLSX
  prompts/
    invoice_curator.py  Invoice curator system prompt
    family_accounting.py  Family accounting system prompt
    notion_assistant.py   Optional Notion prompt
  db/
    models.py           All ORM models
    session.py          SessionLocal factory
    migrations/versions/ Alembic migration files
  admin/                Admin panel backend + static UI
  mailer/               Gmail SMTP wrapper
  pipeline/             Image processing (Gemini OCR)
  utils/                Rate limiter, date format utils

## BLUEPRINTS
id:invoice_curator   system_prompt→prompts/invoice_curator.py   tools→invoice+automation+export
id:family_accounting system_prompt→prompts/family_accounting.py tools→accounting+split+automation+export

Blueprint fields: id, display_name, system_prompt, model(default:claude-sonnet-4-6),
  tools_enabled(JSON[]str), max_tool_turns(default:6), context_window(default:8),
  context_idle_reset_minutes(default:60)
tools_list() → list[str]

## ROUTING
GroupRegistry: group_jid → blueprint_id, status(active/paused), trigger_type(always|mention|prefix),
  trigger_prefix, custom_instructions, group_type(personal|shared|sys_admin|unregistered)
Router.resolve(db, group_jid) → (Blueprint, GroupRegistry)
Router.check_trigger(entry, text, bot_phone) → bool

## WEBHOOK PAYLOAD (POST /webhook, Bearer auth)
type: "text"|"image"|"participant_update"
jid, sender, messageId, isAdmin(ignored→computed from AdminNumbers), text, imageBase64,
mimeType, caption, pushName, action("add"|"remove"|"leave"), participants

## AGENT LOOP (agent_runner.py AgentRunner.run())
1. load history from ContextStore (idle-reset aware)
2. build system prompt: blueprint.system_prompt + participant_block + custom_instructions + date/is_admin/phone
3. build tool_schemas from registry (filtered by is_admin + allowed_tools)
4. check multi-party confirmation → intercept if pending
5. check single-action confirmation → intercept if pending
   confirmed: execute via registry → feed result to Claude as synthetic tool_use/tool_result → Claude generates reply → save via add_turn
   cancelled: save via context.add x2
6. main loop: call Claude API, handle tool_use (parallel execution), repeat up to max_tool_turns
7. end_turn: extract text reply, save full turn via context.add_turn(turn_msgs), log RequestLog

## CONTEXT STORE (agent/context.py)
GroupContext: messages list of dicts, persisted to ConversationHistory.messages_json
add(role, content, max_pairs) — simple append+trim (used for text-only turns)
add_turn(turn_msgs, max_pairs) — atomic add, trims at turn boundaries (_is_turn_start)
  turn_msgs = [user_text, asst_tool_use?, user_tool_result?..., asst_text]
  _is_turn_start: role=user AND content is str or not tool_result
get_history(max_pairs, idle_minutes) → list[dict] (empty if stale)
idle reset: IDLE_MINUTES=60 (override per blueprint via context_idle_reset_minutes)
MAX_TURNS=8

## TOOL REGISTRY (tool_registry.py)
_tools: {name: {schema:dict, executor:async fn}}
_schema_cache: {frozenset(names): list[dict]}
register(tools_dict) — clears cache
get_schemas(tool_names) — returns Claude-compatible schemas (strips access/category fields)
get_allowed_tool_names(names, is_admin) — filters by schema.access (user|admin|internal)
execute(tool_name, params, **ctx) → str  [ctx: group_jid, sender, is_admin]
"internal" tools (remove_invoice, send_email) not exposed in get_schemas

## CONFIRMATION FLOWS
Single-action (confirmation.py, in-memory, 5m TTL):
  stage_action tool → ConfirmationStore.set(group_jid, action, params, description, staged_by)
  yes/כן/confirm/אישור → execute → Claude generates natural reply → add_turn
  no/לא/cancel/ביטול → clear → add x2
  staged_by enforcement: only requester can confirm

Multi-party (multi_confirmation.py, DB CrossGroupConfirmation, 5m TTL):
  awaits phones[] each confirm/reject independently
  all confirmed → commit_action via registry
  any rejection → cancel all
  scheduler job _expire_multi_confirmations (every 60s)

## TOOLS SUMMARY
### invoice_curator tools
get_status — group config (language,header,author,dual_currency)
list_invoices — invoices for month (vendor,date,amount,flag,uuid)
get_invoice_summary — count,total_ils,flagged count
update_config — admin: change settings
flag_invoice / unflag_invoice — admin: review flag
set_invoice_date — admin: correct date (no confirm needed)
save_invoice — admin: manual text entry
stage_action — stages: remove_invoice|send_email|set_invoice_amount|add_date_format
set_invoice_amount — confirmed via stage_action
add_date_format — confirmed via stage_action
export_invoice_report — PDF/XLSX, optional images; group or email delivery
[internal] remove_invoice — confirmed executor (access:internal)
[internal] send_email — confirmed executor (access:internal)
create_automation / activate_automation / list_automations / pause_automation / cancel_automation / edit_automation — admin

### family_accounting tools
record_expense, record_payment, get_balance, get_debt_summary, get_history
get_transaction, set_reminder, list_reminders, cancel_reminder
set_report_email, list_participants, rename_participant
correct_transaction, commit_correction, export_accounting_report, record_split
create_automation / activate_automation / list_automations / pause_automation / cancel_automation / edit_automation

### send_email tool
send_email (custom email with {{template_vars}}, admin only; requires EmailAllowlist)

## AUTOMATION ENGINE
AutomationRule fields: group_jid, rule_type(one_off|recurring|inactivity|threshold|event_trigger),
  trigger_config(JSON), action_config(JSON), status(pending_confirm|active|paused|completed|cancelled)

Action types: send_message | run_agent_action | workflow(list of steps)
Metrics for threshold: monthly_invoice_total, invoice_count_this_month, open_debt_amount,
  days_since_last_settlement
Template vars: {{previous_month}}, {{previous_month_name}}, {{previous_month_number}},
  {{previous_month_year}}, {{current_month}}, {{current_month_number}}, {{current_year}},
  {{today}}, {{monthly_invoice_total}}, {{previous_month_invoice_total}},
  {{open_debt_amount}}, {{invoice_count_this_month}}, {{group_jid}}
Scheduler jobs (APScheduler async): _dispatch_due_messages, _fire_recurring_rules,
  _expire_multi_confirmations, _fire_threshold_rules

## DB MODELS (db/models.py)
Invoice — group_jid, invoice_date, vendor, amount_ils, amount_usd, image_hash, flags, uuid
GroupConfig — group_jid PK, language(en|he), header, author, dual_currency bool
GroupParticipant — group_jid+phone PK, push_name, admin_name, household, status
AdminNumbers — phone PK
ConversationHistory — group_id PK, messages_json, last_active
AutomationRule — id, group_jid, rule_type, trigger_config(JSON), action_config(JSON), status
LedgerEntry — id, group_jid, from_phone, to_phone, amount, currency, description, settled
LedgerSettlement — id, payment_id, debt_id, amount (FIFO links)
SplitTransaction — id, group_jid, payer_phone, total_amount, shares(JSON)
CrossGroupConfirmation — id, action_id, group_jid, pending_phones(JSON), confirmed_phones(JSON), expires_at
UserAccount — id, phone, group_jid, display_name
UserProfile — phone PK, email, display_name
SystemConfig — key PK, value (JSON)
RequestLog — id, group_jid, blueprint_id, tools_called(JSON), duration_ms, stop_reason, error
ReportFormat — id, group_jid, format_type, template(JSON)
ExchangeRateCache — currency+date PK, source, rate, fetched_at
EmailAllowlist — email PK, added_by
GroupRegistry — group_jid PK, blueprint_id FK, status, trigger_type, trigger_prefix, custom_instructions, group_type
ScheduledMessage — id, group_jid, target_phone, message, send_at, sent bool
Blueprint — id PK, display_name, system_prompt, model, tools_enabled(JSON), max_tool_turns, context_window, context_idle_reset_minutes

## SECURITY
Webhook auth: Authorization: Bearer {WEBHOOK_SECRET}
is_admin computed server-side from AdminNumbers (bridge isAdmin untrusted)
Phone validation: ^\d{7,18}$
Tool access: schema.access ∈ user|admin|internal; registry filters per call
EmailAllowlist: deny-all by default; checked in send_email executor
Admin UI: JWT-protected (admin_ui_password, admin_jwt_secret)

## CONFIG (.env keys)
bridge_url, bridge_secret, database_url
anthropic_api_key, claude_model
gemini_api_key, gemini_model
gmail_user, gmail_app_password
r2_endpoint, r2_access_key_id, r2_secret_access_key, r2_bucket, r2_public_url
bot_phone_number, admin_phone_number, legacy_group_jid
notion_api_key, notion_tasks_database_id
admin_ui_password, admin_jwt_secret
image_max_px(1920), image_jpeg_quality(85), confidence_flag_threshold(0.6)
DEFAULT_REPORT_EMAIL (fallback for export delivery)

## DOCKER
services: bridge(./bridge, :3001), orchestrator(./orchestrator, :8080)
volumes: ./data/auth, ./data/db
orchestrator depends_on bridge healthy

## DEPLOYMENT
Server: root@178.105.63.248 at /opt/whatsapp
Deploy: git pull && docker compose up --build -d
Admin panel: :8080/admin
Git remote: https://github.com/eranitzko/WhatsApp-Agent-Engine (branch: feat/whatsapp-agent-engine)
Always push to GitHub before deploying.
