# Family Accounting Agent — Design Spec

## Goal

A WhatsApp-native family ledger: track who paid what for whom, apply FIFO partial payments, maintain running net balances, and export or remind on demand. Runs as a second blueprint (`family_accounting`) on the existing WhatsApp Agent Engine alongside `invoice_curator`.

## Constraints

- Fixed set of family members (phones known at deploy time)
- All amounts stored in ILS; foreign currency converted at transaction-day rate
- Sender identity resolved from WhatsApp `from_phone` — no self-identification needed
- Reminders are self-service only (sender sets reminders for themselves)
- XLSX export delivered by email; one-time historical import via CLI script
- No new infrastructure — shares existing orchestrator, DB, bridge, AgentRunner

---

## Data Model

### Migration 006 — two new tables

#### `ledger_entries`

One row per **leg** of a transaction. A split creates multiple legs sharing a `transaction_id`.

| column | type | notes |
|---|---|---|
| `id` | TEXT (uuid) | primary key |
| `transaction_id` | TEXT (uuid) | groups all legs of one event |
| `group_jid` | TEXT | which WhatsApp group |
| `from_phone` | TEXT | who paid / who the debt originates from |
| `to_phone` | TEXT | who is owed |
| `amount_ils` | REAL | always ILS after conversion |
| `amount_settled_ils` | REAL | updated as payments are applied; default 0 |
| `description` | TEXT | free text; includes original currency/amount if converted |
| `transaction_date` | DATE | day of transaction (used for FX rate lookup) |
| `created_at` | TIMESTAMP | UTC |

`remaining_ils` is computed: `amount_ils - amount_settled_ils`. A leg is fully settled when `remaining_ils = 0`.

#### `ledger_settlements`

Maps payment legs to the debt legs they cover. Created by FIFO settlement logic.

| column | type | notes |
|---|---|---|
| `id` | TEXT (uuid) | primary key |
| `payment_leg_id` | TEXT | FK → `ledger_entries.id` |
| `debt_leg_id` | TEXT | FK → `ledger_entries.id` |
| `amount_ils` | REAL | portion of this payment applied to this debt |
| `created_at` | TIMESTAMP | UTC |

#### `scheduled_messages`

Stores pending reminders for APScheduler to dispatch.

| column | type | notes |
|---|---|---|
| `id` | TEXT (uuid) | primary key |
| `group_jid` | TEXT | destination group |
| `to_phone` | TEXT | sender who set the reminder |
| `message` | TEXT | reminder text |
| `send_at` | TIMESTAMP | UTC; when to fire |
| `sent` | BOOLEAN | false until dispatched |
| `created_at` | TIMESTAMP | UTC |

---

## FIFO Settlement Logic

When `record_payment(payer, payee, amount)` is called:

1. Query all open debt legs where `from_phone = payee` and `to_phone = payer`, ordered by `transaction_date ASC` (FIFO).
2. Walk the list, applying the payment amount greedily:
   - If `remaining_ils ≤ remaining_payment`: fully settle this leg, subtract from remaining payment, continue.
   - If `remaining_ils > remaining_payment`: partially settle this leg, zero out remaining payment, stop.
3. For each settlement applied, insert a row into `ledger_settlements`.
4. Update `amount_settled_ils` on each affected `ledger_entries` row.
5. Create a new `ledger_entries` leg for the payment itself (direction: payer → payee).

---

## Tools

### `record_transaction`

Records a debt or split from natural language.

- Claude extracts: payer, participant(s), amount, currency, description, date (defaults to today).
- If currency ≠ ILS: fetch day rate from `api.exchangerate.host/convert` (free, no API key), compute ILS equivalent, append original amount to description.
- For splits: divide amount equally among participants (or per stated shares), create one leg per participant.
- Each set of legs shares a generated `transaction_id`.

### `record_payment`

Records a payment and runs FIFO settlement.

- Inputs: payer phone, payee phone, amount ILS, date.
- Runs settlement logic described above.
- Returns: which debts were settled (fully or partially) and remaining open balance.

### `get_balance`

- With two phones: returns net balance between them (positive = A owes B).
- With one phone: returns summary of all open balances that person is party to.

### `get_history`

- Returns itemized ledger entries filtered by person and/or date range.
- Includes description, original amount, settlement status.

### `export_ledger`

- Generates an XLSX with two sheets: **Balances** (net per pair) and **Transactions** (full itemized log).
- Emails to the configured address (reuses existing mailer infrastructure).

### `set_reminder`

- Creates a row in `scheduled_messages` for the sender's own phone only.
- Agent enforces "self only" via system prompt — not in tool code.
- APScheduler polls `scheduled_messages` every minute, sends due messages via bridge `/send`, marks `sent = true`.

---

## Currency Conversion

- Endpoint: `https://api.exchangerate.host/convert?from=USD&to=ILS&date=YYYY-MM-DD&amount=X`
- Called synchronously inside `record_transaction` when currency ≠ ILS.
- If the API is unavailable: return an error asking the user to provide the ILS amount manually.
- Original currency and amount appended to `description` for auditability.

---

## Historical Import (One-Time CLI Script)

`tools/import_ledger.py` — run manually once after deployment.

- Reads the existing XLSX (column mapping TBD once file is shared).
- Maps rows to `ledger_entries` inserts, one `transaction_id` per source row.
- Runs FIFO settlement on the imported data to reconstruct current balances.
- Idempotent: skips rows already present (matched by description + date + amount).
- Usage: `python tools/import_ledger.py --file ledger.xlsx --group-jid 123@g.us`

---

## APScheduler Integration

- `AsyncIOScheduler` added to the FastAPI lifespan in `main.py`.
- Job: every 60 seconds, query `scheduled_messages WHERE sent = false AND send_at <= now()`, fire each via bridge, mark sent.
- Scheduler started in `lifespan` alongside existing startup logic, shut down on exit.

---

## Blueprint & Seeder

New blueprint row seeded at startup:

```python
Blueprint(
    id="family_accounting",
    display_name="Family Accounting",
    system_prompt=FAMILY_ACCOUNTING_SYSTEM_PROMPT,
    model="claude-sonnet-4-6",
    tools_enabled=json.dumps([
        "record_transaction", "record_payment",
        "get_balance", "get_history",
        "export_ledger", "set_reminder",
    ]),
    trigger_type="always",
    max_tool_turns=5,
)
```

---

## System Prompt

- Hebrew-aware (bilingual like `invoice_curator`).
- Contains the fixed family member list: display name → phone mapping.
- Resolves "I", "אני" to sender's phone; resolves first names to phones.
- Instructs agent: `set_reminder` may only target the sender — never another person.
- Instructs agent: always confirm the parsed transaction back to the user before recording ("Eran paid 300₪ for dinner, split equally between Dana and Yael — shall I record this?").

---

## File Structure

```
orchestrator/
  app/
    db/migrations/versions/006_family_accounting.py   # new tables
    models.py                                          # 3 new ORM models
    tools/
      accounting_tools.py                              # 6 tools
      accounting_fifo.py                              # FIFO settlement logic (pure, testable)
      accounting_fx.py                                # currency conversion
      accounting_export.py                            # XLSX generation
    prompts/
      family_accounting.py                            # system prompt
    scheduler.py                                      # APScheduler setup
    seeder.py                                         # add family_accounting blueprint
    main.py                                           # wire scheduler into lifespan
  tools/
    import_ledger.py                                  # one-time CLI import script
  tests/
    test_accounting_fifo.py                           # FIFO settlement unit tests
    test_accounting_tools.py                          # tool schema + executor tests
    test_accounting_fx.py                             # currency conversion (mocked HTTP)
    test_scheduler.py                                 # scheduler job unit test
```

---

## Testing

- **`test_accounting_fifo.py`**: unit tests for FIFO settlement — full settlement, partial settlement, multi-debt FIFO ordering, overpayment (credit), zero-balance edge case.
- **`test_accounting_tools.py`**: all 6 tools have schema + executor; `get_balance` returns correct net; split creates correct number of legs.
- **`test_accounting_fx.py`**: conversion called when currency ≠ ILS; graceful error when API unavailable (mocked with `unittest.mock`).
- **`test_scheduler.py`**: due messages are sent and marked sent; future messages are not sent.
