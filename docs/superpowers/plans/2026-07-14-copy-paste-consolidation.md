# Copy-Paste Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five confirmed live bugs and consolidate nine classes of copy-pasted business logic (five in production code, three in the test suite) that a 5-agent parallel codebase audit found — all instances of the same failure mode already seen twice this session: the same rule reimplemented in multiple places drifts, and only some copies get fixed when the rule changes.

**Architecture:** No new subsystems. Each task either (a) fixes a bug directly where it lives, or (b) extracts one existing piece of duplicated logic into a single shared function/module and migrates every call site to it. Work sequentially — tasks 1, 3, and 5 all touch `app/tools/accounting_tools.py`; tasks 6–8 all touch `app/pipeline/`; running implementers in parallel would conflict.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest + pytest-asyncio. This plan continues directly from commit `e770c60` (HEAD of this worktree/branch).

**Origin:** Every finding below came from 5 parallel read-only review agents that audited `orchestrator/app/` and `orchestrator/tests/` end-to-end, cross-referenced against the actual current code (this plan's author re-read every file below directly in this worktree before writing tasks — line numbers and code snippets are verified against HEAD, not copied from the audit reports uncritically).

---

## Task 1: Shared sender-phone resolution — fixes 2 live LID bugs, 1 admin-command bug, consolidates 5+ duplicate call sites

**Problem:** `ctx.get("resolved_phone")` is the LID-safe, resolved canonical phone that `app/agent_runner.py` computes once per turn and passes into every tool executor. Several places ignore it and re-derive a phone from the raw WhatsApp sender JID instead — which is the opaque LID (not the phone number) in shared groups, exactly the bug class fixed in commits `f8aafa1` and `496dc24` earlier this session. Two call sites don't even attempt the `resolved_phone` fallback at all:

- `app/tools/split_tools.py:65-66` — unconditionally raw:
  ```python
  sender = kwargs.get("sender", "")
  sender_phone = sender.split("@")[0].split(":")[0]
  ```
  This `sender_phone` becomes `reporter_phone` in `_account_service.process_split(...)` (line 94) — a real ledger-identity attribution bug for split-bill records in shared groups.
- `app/export/tool.py:56` — same gap:
  ```python
  sender_phone: str = (ctx.get("sender", "")).split("@")[0].split(":")[0]
  ```
  Feeds `_resolve_email(params, sender_phone)` → `db.get(UserProfile, sender_phone)` (line 23) — a wrong-phone lookup silently falls through to `settings.default_report_email or settings.gmail_user` (line 27), which can send an admin's report to the wrong inbox with no error.
- `app/main.py:304-306` — the admin slash-command path calls `resolve_inbound` too *late*:
  ```python
  if command_handler.is_command(text):
      sender_phone = payload.sender.split("@")[0].split(":")[0]
      reply = await command_handler.handle(db, payload.jid, sender_phone, text)
      router.invalidate(payload.jid)
      return
  ```
  `account_service.resolve_inbound(db, payload.jid, payload.sender)` doesn't run until line 319 — *after* this block already returned. `command_handler.handle`'s admin check (`command_handler.py:22`, `_is_admin` at line 141-142) receives the raw, unresolved phone, so `/bind`, `/unbind`, `/pause`, `/resume`, `/sync` can silently reject a real admin in a shared group.
- `app/tools/accounting_tools.py:60-64` already has a correct local helper:
  ```python
  def _sender_phone(ctx: dict) -> str:
      if resolved := ctx.get("resolved_phone"):
          return resolved
      sender = ctx.get("sender", "")
      return sender.split("@")[0].split(":")[0]
  ```
  but 4 functions in that *same file* bypass it and inline the identical expression anyway: lines 432, 477, 565 (`sender_phone = ctx.get("resolved_phone") or sender.split("@")[0].split(":")[0]`) and line 1290 (`sender_phone: str = ctx.get("resolved_phone") or ctx.get("sender", "").split("@")[0].split(":")[0]`).

**Fix:** One shared function in `app/utils/phone.py` (already the established home for phone-handling utilities — it has `normalize_phone`), used everywhere instead of a local helper or inline expression.

**Files:**
- Modify: `orchestrator/app/utils/phone.py`
- Modify: `orchestrator/app/tools/split_tools.py:65-66`
- Modify: `orchestrator/app/export/tool.py:56`
- Modify: `orchestrator/app/main.py` (move `resolve_inbound` call earlier, lines ~300-321)
- Modify: `orchestrator/app/tools/accounting_tools.py` (delete `_sender_phone`, lines 432, 477, 565, 1290, and all call sites of `_sender_phone(ctx)`)
- Modify: `orchestrator/app/agent/tools.py:721-724` (already uses the pattern correctly, but inline — migrate to shared function for consistency)
- Modify: `orchestrator/app/tools/invoice_tools.py:135-137` (same)
- Test: `orchestrator/tests/test_split_tools.py`
- Test: `orchestrator/tests/test_export_tool.py`
- Test: `orchestrator/tests/test_command_handler.py`
- Test: `orchestrator/tests/test_phone.py` (new)

- [ ] **Step 1: Write the failing test for the shared helper**

Create `orchestrator/tests/test_phone.py`:

```python
from app.utils.phone import resolve_sender_phone


def test_resolve_sender_phone_prefers_resolved_phone():
    ctx = {"resolved_phone": "972523206175", "sender": "175715853041683@lid"}
    assert resolve_sender_phone(ctx) == "972523206175"


def test_resolve_sender_phone_falls_back_to_raw_sender():
    ctx = {"sender": "972523206175@s.whatsapp.net"}
    assert resolve_sender_phone(ctx) == "972523206175"


def test_resolve_sender_phone_falls_back_to_raw_lid_when_unresolved():
    ctx = {"sender": "6541369471061@lid"}
    assert resolve_sender_phone(ctx) == "6541369471061"


def test_resolve_sender_phone_empty_ctx_returns_empty_string():
    assert resolve_sender_phone({}) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_phone.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_sender_phone'`

- [ ] **Step 3: Add `resolve_sender_phone` to `app/utils/phone.py`**

Append to `orchestrator/app/utils/phone.py`:

```python
def resolve_sender_phone(ctx: dict) -> str:
    """Return the resolved canonical phone for the current tool-call context.

    Prefers ctx["resolved_phone"] (the LID-safe phone agent_runner already
    computed via resolve_inbound) over re-deriving one from the raw
    sender JID — that raw fallback is only correct for phone-format senders;
    in shared groups WhatsApp sends an opaque LID instead, and using it
    directly misattributes ledger entries / lookups to the wrong identity.
    Falls back to the raw sender split only when resolved_phone is absent.
    """
    if resolved := ctx.get("resolved_phone"):
        return resolved
    sender = ctx.get("sender", "")
    return sender.split("@")[0].split(":")[0] if sender else ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_phone.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/utils/phone.py orchestrator/tests/test_phone.py
git commit -m "feat: add shared resolve_sender_phone helper"
```

- [ ] **Step 6: Write the failing regression test for split_tools.py**

Add to `orchestrator/tests/test_split_tools.py` (check existing imports/fixtures in that file first — it already has a `db` fixture and an `_account_service`-mocking pattern; match its conventions):

```python
@pytest.mark.asyncio
async def test_execute_record_split_uses_resolved_phone_over_raw_sender(db, monkeypatch):
    """Regression: sender_phone must come from ctx["resolved_phone"], not a raw
    LID split — using the raw LID misattributes reporter_phone in shared groups."""
    from app.tools import split_tools
    captured = {}

    class _FakeAccountService:
        async def process_split(self, **kwargs):
            captured["reporter_phone"] = kwargs["reporter_phone"]
            return object()

    split_tools.set_account_service(_FakeAccountService())
    monkeypatch.setattr(split_tools, "SessionLocal", lambda: db.get_bind().connect() and db)

    tools = split_tools.get_split_tools()
    await tools["record_split"]["executor"](
        {"payer_phone": "972501", "all_phones": ["972501", "972502"],
         "amount": 100, "currency": "ILS", "description": "test"},
        sender="175715853041683@lid",
        resolved_phone="972523206175",
        group_jid="123@g.us",
    )
    assert captured["reporter_phone"] == "972523206175"
```

Note: if `db.get_bind().connect()` / SessionLocal-patching doesn't match this file's actual existing pattern once you read it, use whatever `SessionLocal`-patching convention `test_split_tools.py` already uses elsewhere in the file — don't invent a new one. The point of the test is asserting `reporter_phone == "972523206175"` when `resolved_phone` is set and `sender` is a LID.

- [ ] **Step 7: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_split_tools.py -k resolved_phone -v`
Expected: FAIL — `captured["reporter_phone"]` will be `"175715853041683"` (the raw LID), not `"972523206175"`.

- [ ] **Step 8: Fix `split_tools.py`**

In `orchestrator/app/tools/split_tools.py`, replace:

```python
async def _execute_record_split(params: dict, **kwargs) -> str:
    sender = kwargs.get("sender", "")
    sender_phone = sender.split("@")[0].split(":")[0]
    group_jid = kwargs.get("group_jid", "")
```

with:

```python
async def _execute_record_split(params: dict, **kwargs) -> str:
    from app.utils.phone import resolve_sender_phone
    sender_phone = resolve_sender_phone(kwargs)
    group_jid = kwargs.get("group_jid", "")
```

- [ ] **Step 9: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_split_tools.py -k resolved_phone -v`
Expected: PASS

- [ ] **Step 10: Fix `export/tool.py` with its own regression test**

Add to `orchestrator/tests/test_export_tool.py`:

```python
def test_resolve_email_uses_resolved_phone_over_raw_sender(db):
    """Regression: sender_phone for the UserProfile email lookup must come
    from resolved_phone, not a raw LID split — a mismatch silently falls
    back to the default report email instead of the user's own."""
    from app.db.models import UserProfile
    from app.export.tool import _exec_export_report

    db.add(UserProfile(phone="972523206175", email="eran@example.com"))
    db.commit()

    with patch("app.export.tool.SessionLocal", return_value=_CM(db)), \
         patch("app.export.tool._blueprint_for_group", return_value=None):
        # blueprint_id None short-circuits to "Group not registered." after
        # email resolution runs — sufficient to observe _resolve_email's behavior
        pass

    from app.export.tool import _resolve_email
    email = _resolve_email(
        {}, sender_phone="972523206175",
    )
```

Check `test_export_tool.py`'s existing imports/fixtures (`_CM`, `patch`, `db`) before adding — reuse what's already there rather than re-importing. Given `_resolve_email(params, sender_phone)` takes `sender_phone` directly (not `ctx`), the real regression test belongs one level up, at `_exec_export_report`'s call site. Write it as:

```python
@pytest.mark.asyncio
async def test_export_report_resolves_email_via_resolved_phone(db):
    from app.db.models import UserProfile, GroupRegistry, Blueprint
    from app.export import tool as export_tool

    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(UserProfile(phone="972523206175", email="eran@example.com"))
    db.commit()

    with patch("app.export.tool.SessionLocal", return_value=_CM(db)):
        email = export_tool._resolve_email(
            {}, "972523206175",
        )
    assert email == "eran@example.com"
```

This confirms `_resolve_email` itself works correctly given a resolved phone (it already does — the bug is purely that `_exec_export_report` never passes the resolved one in). The regression must actually exercise `_exec_export_report`'s internal `sender_phone` computation:

```python
@pytest.mark.asyncio
async def test_export_report_uses_resolved_phone_not_raw_lid(db):
    from app.db.models import UserProfile, GroupRegistry, Blueprint
    from app.export import tool as export_tool

    db.add(Blueprint(id="fa2", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="456@g.us", blueprint_id="fa2"))
    db.add(UserProfile(phone="972523206175", email="eran@example.com"))
    db.commit()

    with patch("app.export.tool.SessionLocal", return_value=_CM(db)):
        result = await export_tool._exec_export_report(
            {"format": "pdf", "delivery": "email"},
            is_admin=True, group_jid="456@g.us",
            sender="175715853041683@lid", resolved_phone="972523206175",
        )
    # Before the fix: sender_phone computed from the raw LID never matches
    # UserProfile.phone, so _resolve_email falls through to the default and
    # the group is unregistered for report generation anyway — the
    # observable proxy is that it does NOT return the "no email" error,
    # since a real email WAS found via the resolved phone.
    assert "No email address available" not in result
```

- [ ] **Step 11: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_export_tool.py -k resolved_phone -v`
Expected: FAIL (result contains "No email address available" because `_resolve_email` looked up the raw LID, found no UserProfile, and fell back with no default email configured in test settings)

- [ ] **Step 12: Fix `export/tool.py`**

Replace:

```python
    group_jid: str = ctx.get("group_jid", "")
    sender_phone: str = (ctx.get("sender", "")).split("@")[0].split(":")[0]
```

with:

```python
    from app.utils.phone import resolve_sender_phone
    group_jid: str = ctx.get("group_jid", "")
    sender_phone: str = resolve_sender_phone(ctx)
```

- [ ] **Step 13: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_export_tool.py -k resolved_phone -v`
Expected: PASS

- [ ] **Step 14: Fix `main.py`'s command-handler ordering, with a regression test**

Add to `orchestrator/tests/test_command_handler.py` (check its existing fixtures/imports first and match conventions — it likely has a way to seed `AdminNumbers` and invoke `main._process` or `command_handler.handle` directly; if `main._process` is awkward to test in isolation given webhook payload construction, test at the `command_handler.handle` level instead, asserting that when called with a *resolved* phone it succeeds, and note in a comment that the actual regression (main.py passing the wrong phone) is covered by inspecting main.py's diff — do not force an awkward end-to-end webhook test if the existing test file's pattern doesn't support one cleanly):

```python
@pytest.mark.asyncio
async def test_command_handler_admin_check_uses_canonical_phone(db):
    """/bind and friends must be checked against the canonical phone, not a
    raw LID — this test documents the contract command_handler.handle relies
    on; the actual fix (passing the resolved phone) lives in main.py's
    _process, where resolve_inbound must run before the command_handler
    dispatch, not after."""
    from app.db.models import AdminNumbers
    from app.command_handler import CommandHandler

    db.add(AdminNumbers(phone_number="972523206175"))
    db.commit()

    handler = CommandHandler()
    # Canonical phone (post-resolution) is recognized as admin
    assert handler._is_admin(db, "972523206175") is True
    # Raw LID (pre-resolution) is NOT recognized — proving why main.py must
    # resolve before calling handle(), not pass the raw sender split.
    assert handler._is_admin(db, "175715853041683") is False
```

This test passes today (it's just documenting `_is_admin`'s existing, correct behavior) — the actual bug is in `main.py`'s call-site ordering, which this test can't exercise without a full webhook harness. Proceed to fix `main.py` directly and verify manually via code inspection + the full test suite staying green; do not force a synthetic end-to-end test here if the existing test infrastructure doesn't support constructing a full `WebhookPayload` easily. Check `test_command_handler.py` for `WebhookPayload` or `_process` usage first — if such a harness already exists there, use it for a real regression test instead of the documentation-only test above.

- [ ] **Step 15: Fix `main.py`**

In `orchestrator/app/main.py`, move the `resolve_inbound` call from its current position (right before the cross-group yes/no intercept) to immediately after `text = payload.text or payload.caption or ""` and before the `command_handler.is_command(text)` check. Current code (in order):

```python
        text = payload.text or payload.caption or ""

        # Commands are checked before blueprint lookup (/bind works on unregistered groups).
        # Invalidate the route cache after any command so the next message sees fresh state.
        if command_handler.is_command(text):
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            reply = await command_handler.handle(db, payload.jid, sender_phone, text)
            if reply:
                await _send(payload.jid, reply)
            router.invalidate(payload.jid)
            return

        # Ignore messages sent by the bot itself (bridge may echo outbound messages)
        _bot_phone = settings.bot_phone_number or ""
        if _bot_phone and payload.sender.split("@")[0].split(":")[0] == _bot_phone:
            return

        # Resolve inbound identity before blueprint gate — needed for confirmation intercept
        # on groups that may not be registered (counterpart's private group with LID sender).
        _inbound_phone, _inbound_household_id = account_service.resolve_inbound(
            db, payload.jid, payload.sender
        )
```

Replace with:

```python
        text = payload.text or payload.caption or ""

        # Resolve inbound identity before EVERYTHING below, including the
        # command-handler dispatch — command_handler's admin check must see
        # the resolved canonical phone, not a raw LID, or a real admin in a
        # shared group gets silently rejected from /bind, /pause, etc.
        _inbound_phone, _inbound_household_id = account_service.resolve_inbound(
            db, payload.jid, payload.sender
        )

        # Commands are checked before blueprint lookup (/bind works on unregistered groups).
        # Invalidate the route cache after any command so the next message sees fresh state.
        if command_handler.is_command(text):
            _command_sender_phone = _inbound_phone or payload.sender.split("@")[0].split(":")[0]
            reply = await command_handler.handle(db, payload.jid, _command_sender_phone, text)
            if reply:
                await _send(payload.jid, reply)
            router.invalidate(payload.jid)
            return

        # Ignore messages sent by the bot itself (bridge may echo outbound messages)
        _bot_phone = settings.bot_phone_number or ""
        if _bot_phone and payload.sender.split("@")[0].split(":")[0] == _bot_phone:
            return
```

(The `_inbound_phone, _inbound_household_id = account_service.resolve_inbound(...)` block that used to appear right before the cross-group yes/no intercept is now gone from there — it's been moved up. Everything below that point that referenced `_inbound_phone`/`_inbound_household_id` keeps working unchanged since those variables are now assigned earlier in the same scope.)

- [ ] **Step 16: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all tests pass (this is a reordering, not a behavior change for any already-tested path — if anything breaks, it's likely a test that constructs a payload for a command message and asserts something about `resolve_inbound` NOT being called; investigate and fix that test's assumption, don't revert the fix)

- [ ] **Step 17: Consolidate the remaining known-correct-but-duplicated call sites**

In `orchestrator/app/tools/accounting_tools.py`:
- Delete the `_sender_phone` function (lines 60-64).
- Replace every call site of `_sender_phone(ctx)` with `resolve_sender_phone(ctx)` (there are ~12 call sites — use a project-wide search-and-replace within this file: `_sender_phone(ctx)` → `resolve_sender_phone(ctx)`).
- Replace the 4 inline duplicated expressions:
  - Line 432: `sender_phone = ctx.get("resolved_phone") or sender.split("@")[0].split(":")[0]` → `sender_phone = resolve_sender_phone(ctx)` (the `sender = ctx.get("sender", "")` line immediately above becomes dead — remove it if nothing else in that function uses the bare `sender` variable; check each of the 3 call sites individually, since some may use `sender` for other purposes below).
  - Line 477: same replacement.
  - Line 565: same replacement.
  - Line 1290: `sender_phone: str = ctx.get("resolved_phone") or ctx.get("sender", "").split("@")[0].split(":")[0]` → `sender_phone: str = resolve_sender_phone(ctx)`.
- Add the import at the top of the file: `from app.utils.phone import resolve_sender_phone`.

In `orchestrator/app/agent/tools.py`, find the `resolved_phone or (sender.split("@")[0].split(":")[0] if sender else "")` expression (added a few commits ago as part of the `staged_by` fix) and replace it with `resolve_sender_phone({"resolved_phone": resolved_phone, "sender": sender})` — or, more simply, since that function receives `resolved_phone` and `sender` as separate named parameters rather than a `ctx` dict, add a small direct call: read the current function signature first (it's `exec_request_confirmation(group_id, is_admin, action, params, description, sender="", resolved_phone="", **_)`) and replace the body's fallback line with a call to `resolve_sender_phone({"resolved_phone": resolved_phone, "sender": sender})`.

In `orchestrator/app/tools/invoice_tools.py`, find `staged_by = ctx.get("resolved_phone") or (sender_raw.split("@")[0].split(":")[0] if sender_raw else "")` and replace with `staged_by = resolve_sender_phone(ctx)`, removing the now-unused `sender_raw = ctx.get("sender", "")` line above it if nothing else in the function needs the raw value (check first).

- [ ] **Step 18: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all tests pass unchanged (pure refactor, behavior-preserving)

- [ ] **Step 19: Commit**

```bash
git add orchestrator/app/tools/split_tools.py orchestrator/app/export/tool.py orchestrator/app/main.py orchestrator/app/tools/accounting_tools.py orchestrator/app/agent/tools.py orchestrator/app/tools/invoice_tools.py orchestrator/tests/test_split_tools.py orchestrator/tests/test_export_tool.py orchestrator/tests/test_command_handler.py
git commit -m "fix: use resolved canonical phone everywhere, not raw sender JID/LID

Fixes two live LID-attribution bugs (record_split misattributing
reporter_phone, export_report resolving the wrong email) and a live admin-
command bug (/bind and friends checked against an unresolved phone because
main.py ran resolve_inbound after, not before, the command dispatch).
Consolidates 8+ inline/local re-implementations of the same
resolved-phone-or-raw-fallback expression into one app.utils.phone.resolve_sender_phone."
```

---

## Task 2: Bilateral netting in `get_debt_summary`

**Problem:** `app/tools/accounting_tools.py`'s `_exec_get_debt_summary` (lines 692-741) aggregates strictly per directed `(from_phone, to_phone)` key and never nets the reverse direction — unlike `_exec_get_balance` (lines 615-689), which computes `owes - owed` for a pair. If A owes B ₪100 on one entry and B owes A ₪30 on another, `get_balance` reports "A owes B: ₪70" but `get_debt_summary` prints *both* "A owes B: ₪100" and "B owes A: ₪30" as separate lines — directly contradicting `get_balance` for the same data.

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py:692-741`
- Test: `orchestrator/tests/test_accounting_tools.py`

- [ ] **Step 1: Write the failing test**

Find `test_accounting_tools.py`'s existing pattern for testing `_exec_get_debt_summary` or `_exec_get_balance` (it should already seed `LedgerEntry` rows and call the executor — match that exact pattern). Add:

```python
@pytest.mark.asyncio
async def test_get_debt_summary_nets_bilateral_debts(db):
    """Regression: A owes B 100 and B owes A 30 on separate entries must net
    to a single 'A owes B: 70' line, matching get_balance's netting — not
    two separate, contradictory lines."""
    from datetime import date
    from decimal import Decimal
    from app.db.models import LedgerEntry
    from app.tools.accounting_tools import get_accounting_tools

    db.add(LedgerEntry(
        transaction_id="tx1", group_jid="123@g.us", entry_type="debt",
        from_phone="972501", to_phone="972502",
        amount_ils=Decimal("100"), amount_settled_ils=Decimal("0"),
        transaction_date=date(2026, 7, 1),
    ))
    db.add(LedgerEntry(
        transaction_id="tx2", group_jid="123@g.us", entry_type="debt",
        from_phone="972502", to_phone="972501",
        amount_ils=Decimal("30"), amount_settled_ils=Decimal("0"),
        transaction_date=date(2026, 7, 2),
    ))
    db.commit()

    tools = get_accounting_tools()
    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["get_debt_summary"]["executor"](
            {}, group_jid="123@g.us", is_admin=True, sender="972501@s.whatsapp.net",
        )

    assert "972501 owes 972502: ₪70.00" in result
    assert "972502 owes 972501" not in result
```

Check `test_accounting_tools.py` for its existing `_CM` class / `SessionLocal`-patching convention and `from unittest.mock import patch` import — reuse what's there.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_accounting_tools.py -k bilateral -v`
Expected: FAIL — result contains both "972501 owes 972502: ₪100.00" and "972502 owes 972501: ₪30.00" as separate lines, not one netted "₪70.00" line.

- [ ] **Step 3: Fix `_exec_get_debt_summary`**

Replace the aggregation and output section (from `# Aggregate net per (debtor, creditor) pair` through the end of the function):

```python
    # Aggregate net per (debtor, creditor) pair
    net: dict = defaultdict(Decimal)
    oldest: dict = {}
    for r in rows:
        key = (r.from_phone, r.to_phone)
        net[key] += r.amount_ils - (r.amount_settled_ils or Decimal("0"))
        if key not in oldest or r.transaction_date < oldest[key]:
            oldest[key] = r.transaction_date

    lines = []
    for (debtor, creditor), amount in sorted(net.items(), key=lambda x: -x[1]):
        if amount <= Decimal("0"):
            continue
        lines.append(
            f"{debtor} owes {creditor}: ₪{float(amount):,.2f} "
            f"(since {oldest[(debtor, creditor)]})"
        )
    if not lines:
        return "No open debts." if is_admin else "You have no open debts."
    return "\n".join(lines)
```

with:

```python
    # Aggregate gross remaining per directed (debtor, creditor) pair
    gross: dict = defaultdict(Decimal)
    oldest: dict = {}
    for r in rows:
        key = (r.from_phone, r.to_phone)
        gross[key] += r.amount_ils - (r.amount_settled_ils or Decimal("0"))
        if key not in oldest or r.transaction_date < oldest[key]:
            oldest[key] = r.transaction_date

    # Bilaterally net each pair (A,B) against (B,A) into one signed line —
    # matching get_balance's netting. Without this, the same pair can show
    # up as two separate, contradictory "A owes B" / "B owes A" lines.
    seen_pairs: set = set()
    net_lines: list = []
    for (a, b) in gross:
        pair = frozenset((a, b))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        forward = gross.get((a, b), Decimal("0"))
        reverse = gross.get((b, a), Decimal("0"))
        diff = forward - reverse
        if diff == Decimal("0"):
            continue
        debtor, creditor = (a, b) if diff > 0 else (b, a)
        amount = abs(diff)
        since = min(d for d in (oldest.get((a, b)), oldest.get((b, a))) if d is not None)
        net_lines.append((amount, debtor, creditor, since))

    if not net_lines:
        return "No open debts." if is_admin else "You have no open debts."

    net_lines.sort(key=lambda x: -x[0])
    lines = [
        f"{debtor} owes {creditor}: ₪{float(amount):,.2f} (since {since})"
        for amount, debtor, creditor, since in net_lines
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_accounting_tools.py -k bilateral -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass — check specifically for any pre-existing `get_debt_summary` tests asserting the OLD (buggy) two-line behavior; if one exists, update its assertion to the corrected netted output (that test was asserting the bug, not a real requirement).

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/tools/accounting_tools.py orchestrator/tests/test_accounting_tools.py
git commit -m "fix: bilaterally net get_debt_summary, matching get_balance

Same pair could print two separate, contradictory 'A owes B' / 'B owes A'
lines instead of one netted line."
```

---

## Task 3: Consolidate `remaining_ils` and fix `accounting_fifo.py`'s null-guard gap

**Problem:** `LedgerEntry.remaining_ils` (`app/db/models.py:169-171`) is the canonical definition:
```python
@property
def remaining_ils(self) -> Decimal:
    return self.amount_ils - (self.amount_settled_ils or Decimal("0"))
```
but the same expression is hand-copied instead of using the property in `app/tools/accounting_export.py` (lines 86, 139, 192, 335, 369 — all `e.amount_ils - (e.amount_settled_ils or Decimal("0"))` where `e` is a `LedgerEntry`). Worse: `app/tools/accounting_fifo.py`'s own `DebtLeg` dataclass (lines 10-19) reimplements it **without** the `or Decimal("0")` guard:
```python
@property
def remaining_ils(self) -> Decimal:
    return self.amount_ils - self.amount_settled_ils
```
If `amount_settled_ils` is ever `None` on a `DebtLeg` (the dataclass itself allows it structurally even though the DB column is `nullable=False` with a default), this raises `TypeError` while every other copy of the same logic silently treats it as zero.

Separately, the pattern "query open `LedgerEntry` rows for a (from,to) pair and build a list of `DebtLeg`" is implemented twice: `app/accounting/account_service.py`'s `_open_debt_legs` method (lines ~498-522, already extracted as a shared method within that file) and `app/agent_runner.py`'s inline block (lines 456-476, inside the `commit_payment` branch of a multi-confirmation handler). These should share one implementation.

**Files:**
- Modify: `orchestrator/app/tools/accounting_fifo.py`
- Modify: `orchestrator/app/tools/accounting_export.py` (5 call sites)
- Modify: `orchestrator/app/agent_runner.py` (1 call site, ~lines 456-476)
- Test: `orchestrator/tests/test_accounting_fifo.py`

- [ ] **Step 1: Write the failing test for the null-guard fix**

Add to `orchestrator/tests/test_accounting_fifo.py`:

```python
def test_debtleg_remaining_ils_guards_none_settled():
    """Regression: DebtLeg.remaining_ils must default a None settled amount
    to zero, matching LedgerEntry.remaining_ils's guard — otherwise this
    diverges into a TypeError the moment amount_settled_ils is ever None,
    while every other copy of this same calculation silently treats it as 0."""
    from datetime import date
    from decimal import Decimal
    from app.tools.accounting_fifo import DebtLeg

    leg = DebtLeg(id="x", amount_ils=Decimal("100"), amount_settled_ils=None, transaction_date=date.today())
    assert leg.remaining_ils == Decimal("100")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_accounting_fifo.py -k none_settled -v`
Expected: FAIL with `TypeError: unsupported operand type(s) for -: 'decimal.Decimal' and 'NoneType'`

- [ ] **Step 3: Fix `DebtLeg.remaining_ils`**

In `orchestrator/app/tools/accounting_fifo.py`, replace:

```python
@dataclass
class DebtLeg:
    id: str
    amount_ils: Decimal
    amount_settled_ils: Decimal
    transaction_date: date

    @property
    def remaining_ils(self) -> Decimal:
        return self.amount_ils - self.amount_settled_ils
```

with:

```python
@dataclass
class DebtLeg:
    id: str
    amount_ils: Decimal
    amount_settled_ils: Decimal
    transaction_date: date

    @property
    def remaining_ils(self) -> Decimal:
        # Matches LedgerEntry.remaining_ils's null-guard (app/db/models.py) —
        # this module is deliberately DB-independent (see module docstring),
        # so it can't import that property directly, but the two must stay
        # behaviorally identical or FIFO settlement can raise where every
        # other consumer of the same value silently defaults to zero.
        return self.amount_ils - (self.amount_settled_ils or Decimal("0"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_accounting_fifo.py -k none_settled -v`
Expected: PASS

- [ ] **Step 5: Consolidate `accounting_export.py`'s 5 inline copies**

Read `orchestrator/app/tools/accounting_export.py` around lines 86, 139, 192, 335, 369 first (line numbers may have shifted slightly from other work this session — search for the literal string `amount_ils - (e.amount_settled_ils or Decimal` to find every occurrence). At each occurrence, replace `e.amount_ils - (e.amount_settled_ils or Decimal("0"))` with `e.remaining_ils` (both refer to the same `LedgerEntry` instance `e` in each case — verify this by checking the enclosing loop variable name at each site; if a site uses a different loop variable name than `e`, adjust the property access accordingly, e.g. `entry.remaining_ils`).

- [ ] **Step 6: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass (pure refactor — `e.remaining_ils` and `e.amount_ils - (e.amount_settled_ils or Decimal("0"))` are definitionally identical)

- [ ] **Step 7: Consolidate the duplicated `DebtLeg`-fetching query in `agent_runner.py`**

Read `orchestrator/app/accounting/account_service.py`'s existing `_open_debt_legs` method (search for `def _open_debt_legs` — it was added earlier this session as part of the bilateral-netting payment fix) to see its exact signature. It's a method on `AccountService`, taking `(self, db, group_jid, from_phone, to_phone, household_id)` and returning `list[DebtLeg]`. Promote it to a standalone module-level function in `app/tools/accounting_fifo.py` (next to `DebtLeg`/`apply_payment`, since it's the natural shared home for FIFO-adjacent query logic, and it removes the need for `agent_runner.py` to import from `account_service.py` just for this):

```python
def fetch_open_debt_legs(db, group_jid: str, from_phone: str, to_phone: str, household_id: str | None = None) -> list[DebtLeg]:
    """Query open (partially/fully unsettled) LedgerEntry rows for a directed
    (from_phone, to_phone) pair, ordered oldest-first, as DebtLeg objects
    ready for apply_payment(). Requires DB access, unlike the rest of this
    module — kept here anyway since it's the natural counterpart to
    apply_payment, and this exact query+construction pattern was previously
    duplicated between account_service.py and agent_runner.py."""
    from app.db.models import LedgerEntry
    q = db.query(LedgerEntry).filter(
        LedgerEntry.from_phone == from_phone,
        LedgerEntry.to_phone == to_phone,
        LedgerEntry.amount_ils > LedgerEntry.amount_settled_ils,
    )
    if household_id:
        q = q.filter(LedgerEntry.household_id == household_id)
    else:
        q = q.filter(LedgerEntry.group_jid == group_jid)
    rows = q.order_by(LedgerEntry.transaction_date).all()
    return [
        DebtLeg(
            id=r.id,
            amount_ils=r.amount_ils,
            amount_settled_ils=r.amount_settled_ils or Decimal("0"),
            transaction_date=r.transaction_date,
        )
        for r in rows
    ]
```

Then in `app/accounting/account_service.py`, replace the body of `_open_debt_legs` to delegate to it:
```python
def _open_debt_legs(self, db, group_jid, from_phone, to_phone, household_id):
    from app.tools.accounting_fifo import fetch_open_debt_legs
    return fetch_open_debt_legs(db, group_jid, from_phone, to_phone, household_id)
```
(Keep the method wrapper rather than deleting it and updating every call site, to minimize the diff — check first whether `_open_debt_legs` is called from more than one place in that file; if it's only called internally within the same class, consider whether removing the wrapper and calling `fetch_open_debt_legs` directly at each call site is cleaner. Use judgment; either is acceptable as long as behavior is unchanged and there's exactly one real implementation.)

In `app/agent_runner.py`, replace the inline block (lines ~456-476):
```python
                open_rows = (
                    db.query(LedgerEntry)
                    .filter(
                        LedgerEntry.group_jid == group_jid,
                        LedgerEntry.from_phone == payer,
                        LedgerEntry.to_phone == payee,
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
```
with:
```python
                from app.tools.accounting_fifo import fetch_open_debt_legs
                debt_legs = fetch_open_debt_legs(db, group_jid, payer, payee)
```
(Check whether `agent_runner.py` already imports `DebtLeg` for this block only — if so, and it's unused elsewhere in the file after this change, remove that now-dead import.)

- [ ] **Step 8: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass — this changes internal query construction, not behavior; if a test mocked the old inline query shape directly (unlikely, but check `test_agent_runner.py` and `test_account_service.py`), update it to match.

- [ ] **Step 9: Commit**

```bash
git add orchestrator/app/tools/accounting_fifo.py orchestrator/app/tools/accounting_export.py orchestrator/app/agent_runner.py orchestrator/app/accounting/account_service.py orchestrator/tests/test_accounting_fifo.py
git commit -m "fix: guard accounting_fifo.DebtLeg.remaining_ils against None, consolidate remaining_ils/open-debt-query duplication

DebtLeg.remaining_ils diverged from LedgerEntry.remaining_ils by omitting
the None-settled guard — a live TypeError risk. Also consolidates 5 inline
copies of the same expression in accounting_export.py and 2 copies of the
open-debt-legs fetch query (account_service.py, agent_runner.py)."
```

---

## Task 4: Canonical yes/no/confirm/cancel word recognition

**Problem:** Confirm/cancel word recognition is independently implemented in **7 locations across 4 files**, and they already disagree:

- `app/main.py:325` — `("yes", "no", "כן", "לא", "y", "n", "אישור", "ביטול")`
- `app/main.py:379` — `("yes", "no", "כן", "לא", "y", "n")` — **missing** `"אישור"`/`"ביטול"` that the block 50 lines above has. A sys_admin replying "אישור" to approve a pending group registration silently falls through instead of approving it.
- `app/agent/confirmation.py:18-19` — `CONFIRM_WORDS = {"yes", "כן", "confirm", "אישור", "ok", "approve"}` (no `"y"`), `CANCEL_WORDS = {"no", "לא", "cancel", "ביטול", "abort"}` (no `"n"`)
- `app/agent/multi_confirmation.py:26-27` — `CONFIRM_WORDS = {"yes", "כן", "confirm", "אישור", "ok", "approve", "יאללה"}`, `CANCEL_WORDS = {"no", "לא", "cancel", "ביטול", "abort", "reject"}`
- `app/accounting/group_registration.py:99` — `("yes", "כן", "y")` (missing `"confirm"`, `"אישור"`, `"ok"`, `"approve"`)
- `app/accounting/group_registration.py:115` — `("no", "לא", "n")` (missing `"cancel"`, `"ביטול"`, `"abort"`)
- `app/accounting/group_registration.py:132` — `("yes", "no", "כן", "לא", "y", "n")` (same gap as `main.py:379`)

**Fix:** One canonical module with the union of every word recognized anywhere today (don't silently drop a word some existing path already accepts), used by all 7 sites.

**Files:**
- Create: `orchestrator/app/agent/reply_words.py`
- Modify: `orchestrator/app/main.py:325,379`
- Modify: `orchestrator/app/agent/confirmation.py:18-19,71-75`
- Modify: `orchestrator/app/agent/multi_confirmation.py:26-27` and its `is_confirm`/`is_cancel` methods
- Modify: `orchestrator/app/accounting/group_registration.py:99,115,132`
- Test: `orchestrator/tests/test_reply_words.py` (new)

- [ ] **Step 1: Write the failing test**

Create `orchestrator/tests/test_reply_words.py`:

```python
from app.agent.reply_words import is_affirmative, is_negative


def test_is_affirmative_recognizes_union_of_all_known_confirm_words():
    for word in ["yes", "y", "ok", "confirm", "approve", "כן", "אישור", "יאללה", "YES", " yes "]:
        assert is_affirmative(word), f"{word!r} should be affirmative"


def test_is_negative_recognizes_union_of_all_known_cancel_words():
    for word in ["no", "n", "cancel", "abort", "reject", "לא", "ביטול", "NO", " no "]:
        assert is_negative(word), f"{word!r} should be negative"


def test_is_affirmative_rejects_unrelated_text():
    assert not is_affirmative("what is my balance")


def test_is_negative_rejects_unrelated_text():
    assert not is_negative("what is my balance")


def test_word_lists_do_not_overlap():
    from app.agent.reply_words import CONFIRM_WORDS, CANCEL_WORDS
    assert CONFIRM_WORDS.isdisjoint(CANCEL_WORDS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_reply_words.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.reply_words'`

- [ ] **Step 3: Create `app/agent/reply_words.py`**

```python
"""Canonical yes/no reply-word recognition, shared across every
confirmation flow in the app: single-action stage_action
(app/agent/confirmation.py), multi-party confirmations
(app/agent/multi_confirmation.py), cross-group confirmations and sys-admin
group-registration approval (both in app/main.py and
app/accounting/group_registration.py).

Previously each flow re-implemented its own word list independently and
they had already drifted — e.g. one flow didn't recognize "אישור"/"ביטול"
that a sibling flow (50 lines away in the same file) did, silently
rejecting a valid approval reply. This module holds the union of every
word any flow has ever recognized.
"""
from __future__ import annotations

CONFIRM_WORDS = {"yes", "y", "ok", "confirm", "approve", "כן", "אישור", "יאללה"}
CANCEL_WORDS = {"no", "n", "cancel", "abort", "reject", "לא", "ביטול"}


def is_affirmative(text: str) -> bool:
    return text.strip().lower() in CONFIRM_WORDS


def is_negative(text: str) -> bool:
    return text.strip().lower() in CANCEL_WORDS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_reply_words.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/agent/reply_words.py orchestrator/tests/test_reply_words.py
git commit -m "feat: add canonical reply_words module (is_affirmative/is_negative)"
```

- [ ] **Step 6: Write the regression test for the main.py:379 bug**

Check `orchestrator/tests/` for an existing test file covering `group_registration_handler` or the sys_admin registration flow (likely `test_group_registration.py` or similar — search for `is_pending_reply` or `handle_admin_reply`). Add:

```python
def test_is_pending_reply_recognizes_confirmation_word_used_elsewhere(db):
    """Regression: this check must recognize every word the cross-group
    confirmation intercept (app/main.py:325) recognizes — previously it used
    a narrower word list missing 'אישור'/'ביטול', so a sys_admin approving a
    registration with 'אישור' silently fell through instead of approving."""
    # Match this file's existing pattern for staging a pending registration
    # before calling is_pending_reply — read the file first to find the
    # right setup helper (likely something like handler._pending[jid] = {...}
    # or a call to on_bot_added_to_group). Then:
    from app.accounting.group_registration import GroupRegistrationHandler
    handler = GroupRegistrationHandler()
    # ... set up a pending registration for admin_group_jid, matching
    # whatever this test file's existing tests do ...
    assert handler.is_pending_reply(db, "admin_group@g.us", "אישור") is True
```

If no existing test file covers `group_registration.py` at all, create `orchestrator/tests/test_group_registration.py` and check `app/accounting/group_registration.py`'s `GroupRegistrationHandler` class constructor and `_pending` dict shape directly (read the file) to write a correct setup — do not guess the internal state shape without reading it first.

- [ ] **Step 7: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/ -k is_pending_reply -v`
Expected: FAIL

- [ ] **Step 8: Fix all 7 call sites**

In `orchestrator/app/main.py`:
- Line 325: replace `if text.strip().lower() in ("yes", "no", "כן", "לא", "y", "n", "אישור", "ביטול"):` with `if is_affirmative(text) or is_negative(text):` (add `from app.agent.reply_words import is_affirmative, is_negative` to the top-level imports).
- Line 379: replace `if text.strip().lower() in ("yes", "no", "כן", "לא", "y", "n"):` with the same `if is_affirmative(text) or is_negative(text):`.

In `orchestrator/app/agent/confirmation.py`:
- Replace the local `CONFIRM_WORDS`/`CANCEL_WORDS` definitions and `is_confirm`/`is_cancel` methods to delegate:
```python
from app.agent.reply_words import is_affirmative, is_negative

# ... (remove CONFIRM_WORDS/CANCEL_WORDS module-level sets) ...

    def is_confirm(self, text: str) -> bool:
        return is_affirmative(text)

    def is_cancel(self, text: str) -> bool:
        return is_negative(text)
```

In `orchestrator/app/agent/multi_confirmation.py`: same pattern — remove the local `CONFIRM_WORDS`/`CANCEL_WORDS` sets, delegate `is_confirm`/`is_cancel` (or their equivalents, matching whatever the actual method names are in that file — read it first) to `is_affirmative`/`is_negative`.

In `orchestrator/app/accounting/group_registration.py`:
- Line 99: replace `if reply_lower in ("yes", "כן", "y"):` with `if is_affirmative(reply):` — but check: `reply_lower = reply.strip().lower()` is computed once above; `is_affirmative` does its own `.strip().lower()` internally, so pass the raw `reply`, not `reply_lower`, to avoid double-processing (harmless either way since `.strip().lower()` on an already-stripped-lowered string is a no-op, but pass `reply` for clarity).
- Line 115: replace `if reply_lower in ("no", "לא", "n"):` with `if is_negative(reply):`.
- Line 132: replace `return text.strip().lower() in ("yes", "no", "כן", "לא", "y", "n")` with `return is_affirmative(text) or is_negative(text)`.
- Add `from app.agent.reply_words import is_affirmative, is_negative` to this file's imports.

- [ ] **Step 9: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/ -k is_pending_reply -v`
Expected: PASS

- [ ] **Step 10: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass. Pay special attention to any test asserting a specific rejected word (e.g. a test that checks `"maybe"` or some non-confirm word is rejected) — those should still pass since we only ever widened recognized words, never narrowed.

- [ ] **Step 11: Commit**

```bash
git add orchestrator/app/main.py orchestrator/app/agent/confirmation.py orchestrator/app/agent/multi_confirmation.py orchestrator/app/accounting/group_registration.py orchestrator/tests/
git commit -m "fix: consolidate yes/no word recognition into app.agent.reply_words

7 independent word-list implementations across 4 files had already
drifted — main.py's sys-admin registration-reply check was missing
'אישור'/'ביטול' that a sibling check 50 lines above it recognized, so
approving a registration with 'אישור' silently failed."
```

---

## Task 5: Consolidate equal-split rounding, standardize on ROUND_HALF_UP

**Problem:** Equal-split rounding is computed 3 different ways:
- `app/tools/accounting_tools.py:494` (inside `_legacy_record_transaction`) — `per_person = (amount_ils / Decimal(len(participants))).quantize(Decimal("0.01"))` — no rounding mode specified, defaults to `ROUND_HALF_EVEN`.
- `app/tools/accounting_tools.py:1169` area (`_exec_apply_correction` — search for the second occurrence of `.quantize(Decimal("0.01"))` with no rounding kwarg in this file, it's in the correction-application path) — same pattern, same implicit `ROUND_HALF_EVEN`.
- `app/tools/split_tools.py:127-134` (`_compute_shares`) — explicit `ROUND_HALF_UP`.

Splitting the same total among the same people via `record_expense`/`apply_correction` vs. `record_split` can land on a different per-person cent value purely because of which tool computed it. **Scope decision for this task: preserve existing remainder-handling behavior exactly** (none of the 3 current implementations redistribute leftover cents — they just divide and round each share independently; this task fixes the *rounding-mode inconsistency* only, not the separate, larger question of whether remainder cents should be redistributed, which is a product decision out of scope here).

**Files:**
- Modify: `orchestrator/app/tools/accounting_fifo.py`
- Modify: `orchestrator/app/tools/accounting_tools.py` (2 call sites)
- Modify: `orchestrator/app/tools/split_tools.py`
- Test: `orchestrator/tests/test_accounting_fifo.py`

- [ ] **Step 1: Write the failing test**

Add to `orchestrator/tests/test_accounting_fifo.py`:

```python
def test_split_evenly_rounds_half_up():
    """Regression: splitting an amount that lands exactly on a half-cent
    must round up (matching split_tools.py's existing explicit choice) —
    previously two of the three call sites defaulted to ROUND_HALF_EVEN
    instead, silently landing on a different per-person cent value for the
    identical split depending which tool computed it."""
    from decimal import Decimal
    from app.tools.accounting_fifo import split_evenly

    # 100.005 / 1 landing exactly on a half-cent boundary after quantization:
    # use an amount/count combination that produces exactly x.xx5.
    result = split_evenly(Decimal("100.005"), 1)
    assert result == [Decimal("100.01")]  # ROUND_HALF_UP, not ROUND_HALF_EVEN (100.00)


def test_split_evenly_returns_one_share_per_person():
    from decimal import Decimal
    from app.tools.accounting_fifo import split_evenly

    result = split_evenly(Decimal("100"), 4)
    assert result == [Decimal("25.00")] * 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_accounting_fifo.py -k split_evenly -v`
Expected: FAIL with `ImportError: cannot import name 'split_evenly'`

- [ ] **Step 3: Add `split_evenly` to `accounting_fifo.py`**

```python
from decimal import Decimal, ROUND_HALF_UP


def split_evenly(total: Decimal, n: int, rounding=ROUND_HALF_UP) -> list[Decimal]:
    """Split total into n equal shares, each rounded to 2 decimal places.

    Does NOT redistribute leftover cents from rounding — each share is
    computed independently as (total / n).quantize(...). This matches the
    pre-existing behavior of every caller being consolidated here; whether
    remainder cents should instead be distributed to make shares sum
    exactly to total is a separate product decision, out of scope for this
    consolidation.
    """
    per_person = (total / Decimal(n)).quantize(Decimal("0.01"), rounding=rounding)
    return [per_person] * n
```

(Add this near the top of the file, after the `Decimal`/`date` imports and before `DebtLeg`, or wherever fits the file's existing organization — check the current import line for `Decimal` at the top of the file and extend it rather than adding a duplicate import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_accounting_fifo.py -k split_evenly -v`
Expected: PASS

- [ ] **Step 5: Migrate the 3 call sites**

In `orchestrator/app/tools/accounting_tools.py`, at the `_legacy_record_transaction` site (~line 494), replace:
```python
    per_person = (amount_ils / Decimal(len(participants))).quantize(Decimal("0.01"))
```
with:
```python
    from app.tools.accounting_fifo import split_evenly
    per_person = split_evenly(amount_ils, len(participants))[0]
```
(`split_evenly` returns a list since it's designed for the split_tools.py case where each share is used individually; here only `per_person` — a single value applied to every participant — is needed, so take `[0]`. Check the local `import` conventions in this file — it uses inline function-local imports in some places already (e.g. `from datetime import date as _date` inside `_exec_record_transaction`) — match that style if this call site is also inside a function body.)

Find and fix the second occurrence in this same file (in `_exec_apply_correction` or wherever the plan's earlier investigation found the second `.quantize(Decimal("0.01"))` with no rounding kwarg — search the file for `.quantize(Decimal("0.01"))` without `rounding=` to find it precisely, since the exact surrounding code wasn't re-verified against current HEAD before this plan was written) using the same `split_evenly(...)​[0]` pattern.

In `orchestrator/app/tools/split_tools.py`, replace `_compute_shares`'s two `quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` call sites:
```python
        if total_participants and total_participants > len(non_payer_phones):
            # Equal split across all participants; payer absorbs own share
            per_person = (total / total_participants).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            remaining = total - specified_total
            per_person = (remaining / len(unspecified)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
```
with:
```python
        from app.tools.accounting_fifo import split_evenly
        if total_participants and total_participants > len(non_payer_phones):
            # Equal split across all participants; payer absorbs own share
            per_person = split_evenly(total, total_participants)[0]
        else:
            remaining = total - specified_total
            per_person = split_evenly(remaining, len(unspecified))[0]
```
(This changes split_tools.py's behavior not at all — `ROUND_HALF_UP` was already its explicit choice, now sourced from the shared function instead of inlined twice in this same file.)

- [ ] **Step 6: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass EXCEPT possibly a pre-existing test asserting the old `ROUND_HALF_EVEN` behavior for `_legacy_record_transaction`/`_exec_apply_correction` on a value that lands exactly on a half-cent boundary — if one exists, update its expected value to the new (intentionally corrected) `ROUND_HALF_UP` result, and note in the commit message that this is a deliberate behavior fix, not an accidental regression.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/tools/accounting_fifo.py orchestrator/app/tools/accounting_tools.py orchestrator/app/tools/split_tools.py orchestrator/tests/test_accounting_fifo.py
git commit -m "fix: consolidate equal-split rounding into split_evenly, standardize on ROUND_HALF_UP

record_expense/apply_correction defaulted to ROUND_HALF_EVEN while
record_split used explicit ROUND_HALF_UP — splitting the same total among
the same people via different tools could land on a different per-person
cent value, producing unexplained ledger residue after FIFO settlement."
```

---

## Task 6: Route invoice date extraction through the shared date-parsing engine

**Problem:** `app/pipeline/extractor.py`'s `_validate_and_normalise` (lines 93-119) hand-rolls two regexes requiring an exactly-4-digit year, before falling back to `_try_extra_date_formats`, which uses the shared engine in `app/utils/date_formats.py` (`parse_format_string`/`try_fmt`) that already handles 2-digit years. A date OCR'd with a 2-digit year silently fails extraction unless an admin has manually configured a matching extra format.

**Files:**
- Modify: `orchestrator/app/pipeline/extractor.py:93-119`
- Test: `orchestrator/tests/test_extractor.py`

- [ ] **Step 1: Write the failing test**

Add to `orchestrator/tests/test_extractor.py`:

```python
def test_validate_and_normalise_two_digit_year_parsed_via_shared_engine():
    """Regression: a 2-digit-year date must parse without requiring an
    admin-configured extra format — the shared date_formats.py engine
    already supports 2-digit years; extractor.py's own hardcoded regexes
    didn't, silently failing extraction instead."""
    raw = {"invoice_date": "14/07/26", "vendor": "X", "amount_original": 50, "currency_original": "ILS"}
    out = _validate_and_normalise(raw)
    assert out["invoice_date"] == "2026-07-14"
```

(Check this test file's existing import line for `_validate_and_normalise` — it should already be imported at the top from earlier work this session.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_extractor.py -k two_digit_year -v`
Expected: FAIL — `out["invoice_date"]` is `None` (both hardcoded regexes require `\d{4}` and don't match `26`, and `_try_extra_date_formats` returns `None` since no admin-configured extra format exists in this test's DB).

- [ ] **Step 3: Fix `_validate_and_normalise`**

Replace:

```python
    # invoice_date: coerce to date string YYYY-MM-DD
    raw_date = raw.get("invoice_date")
    if raw_date and isinstance(raw_date, str):
        # Try YYYY-MM-DD (or YYYY/MM/DD, YYYY.MM.DD) first
        match = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw_date)
        if match:
            y, m, d = match.groups()
            try:
                parsed = date(int(y), int(m), int(d))
                out["invoice_date"] = parsed.isoformat()
            except ValueError:
                out["invoice_date"] = None
        else:
            # Fallback: DD/MM/YYYY or DD.MM.YYYY (Israeli/European format)
            match = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", raw_date)
            if match:
                d, m, y = match.groups()
                try:
                    parsed = date(int(y), int(m), int(d))
                    out["invoice_date"] = parsed.isoformat()
                except ValueError:
                    out["invoice_date"] = None
            else:
                extra = _try_extra_date_formats(raw_date)
                out["invoice_date"] = extra.isoformat() if extra else None
    else:
        out["invoice_date"] = None
```

with:

```python
    # invoice_date: try the default formats (ISO, then Israeli DD/MM/YYYY)
    # first, then any admin-configured extra formats — all through the one
    # shared parsing engine in app/utils/date_formats.py, instead of two
    # hardcoded, 4-digit-year-only regexes that silently failed on 2-digit
    # years the shared engine already handles.
    raw_date = raw.get("invoice_date")
    if raw_date and isinstance(raw_date, str):
        parsed = None
        for fmt in _DEFAULT_DATE_FORMATS:
            parsed = try_fmt(raw_date, fmt)
            if parsed:
                break
        if not parsed:
            parsed = _try_extra_date_formats(raw_date)
        out["invoice_date"] = parsed.isoformat() if parsed else None
    else:
        out["invoice_date"] = None
```

Add this module-level constant near the top of the file, after the existing imports (the file already imports `parse_format_string, try_fmt` from `app.utils.date_formats` — verify this import line is still present before adding the constant; it should be unchanged from before this session's other edits):

```python
_DEFAULT_DATE_FORMATS = [parse_format_string("YYYY-MM-DD"), parse_format_string("DD/MM/YYYY")]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_extractor.py -k two_digit_year -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass. Pay attention to any existing extractor date-parsing tests that assumed 4-digit years only or a specific regex-driven edge case — `try_fmt`'s day/month-order handling should behave equivalently for those, but verify.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/pipeline/extractor.py orchestrator/tests/test_extractor.py
git commit -m "fix: route invoice date extraction through shared date_formats engine

extractor.py's two hardcoded date regexes required exactly 4-digit years;
the shared engine already handles 2-digit years. One parsing
implementation now, not two that could silently diverge."
```

---

## Task 7: `to_float_or_none` helper — fix the falsy-zero-amount ambiguity risk

**Problem:** The pattern `float(x) if x else None` (a truthiness check, not `is not None`) — which would silently treat a valid `Decimal("0")` the same as a missing value — appears in `app/pipeline/pipeline.py` (lines 229, 231, 247, 249) and `app/export/generators/invoice.py` (lines 90, 91, 94). This is currently harmless only because `app/utils/invoice_amount.py`'s `validate_invoice_amount` already guarantees invoice amounts are never zero — but that's exactly the kind of cross-file invariant that already drifted once this session (the "must be positive" rule was relaxed in some places before others). If a future change ever allows a zero amount, these ~7 call sites would each need to be found and fixed by hand.

**Files:**
- Modify: `orchestrator/app/utils/invoice_amount.py` (add the helper here, alongside the related amount-validation logic)
- Modify: `orchestrator/app/pipeline/pipeline.py` (4 call sites)
- Modify: `orchestrator/app/export/generators/invoice.py` (3 call sites)
- Test: `orchestrator/tests/test_invoice_amount.py`

- [ ] **Step 1: Write the failing test**

Add to `orchestrator/tests/test_invoice_amount.py`:

```python
def test_to_float_or_none_preserves_valid_zero():
    """Regression: must use `is not None`, not truthiness — a truthy check
    would silently convert a legitimate Decimal('0') to None, indistinguishable
    from a missing value."""
    from decimal import Decimal
    from app.utils.invoice_amount import to_float_or_none

    assert to_float_or_none(Decimal("0")) == 0.0


def test_to_float_or_none_none_stays_none():
    from app.utils.invoice_amount import to_float_or_none
    assert to_float_or_none(None) is None


def test_to_float_or_none_negative_preserved():
    from decimal import Decimal
    from app.utils.invoice_amount import to_float_or_none
    assert to_float_or_none(Decimal("-22.5")) == -22.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/test_invoice_amount.py -k to_float_or_none -v`
Expected: FAIL with `ImportError: cannot import name 'to_float_or_none'`

- [ ] **Step 3: Add `to_float_or_none` to `app/utils/invoice_amount.py`**

Append:

```python
def to_float_or_none(x: Decimal | None) -> float | None:
    """Convert a Decimal to float, preserving None as None — using `is not
    None` rather than truthiness, so a legitimate zero value is never
    silently converted to None (indistinguishable from "missing")."""
    return float(x) if x is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/test_invoice_amount.py -k to_float_or_none -v`
Expected: PASS

- [ ] **Step 5: Migrate `pipeline.py`'s 4 call sites**

In `orchestrator/app/pipeline/pipeline.py`, add `from app.utils.invoice_amount import to_float_or_none` to the imports, and replace (lines 229, 231, in the R2 metadata sidecar dict — note: this dict construction is being replaced wholesale in Task 8, so if Task 8 runs after this task, these two specific lines will be superseded; still make this fix now for correctness and in case task ordering changes):

```python
                "amount_original":     float(amount_original) if amount_original else None,
```
→
```python
                "amount_original":     to_float_or_none(amount_original),
```

```python
                "amount_ils":          float(amount_ils) if amount_ils else None,
```
→
```python
                "amount_ils":          to_float_or_none(amount_ils),
```

(These two appear inside the sidecar dict at lines ~229/231.) And in the function's final return dict (lines 247, 249):

```python
        "amount_original":  float(amount_original) if amount_original else None,
```
→
```python
        "amount_original":  to_float_or_none(amount_original),
```

```python
        "amount_ils":       float(amount_ils) if amount_ils else None,
```
→
```python
        "amount_ils":       to_float_or_none(amount_ils),
```

- [ ] **Step 6: Migrate `export/generators/invoice.py`'s 3 call sites**

In `orchestrator/app/export/generators/invoice.py`, add `from app.utils.invoice_amount import to_float_or_none` to the imports, and replace:

```python
                orig = format_amount(float(r.amount_original) if r.amount_original else None, r.currency_original)
                ils = format_currency(float(r.amount_ils), "₪") if r.amount_ils else "—"
                cells = [date_s, inv_num, vendor, desc, orig, ils]
            else:
                amt = format_amount(float(r.amount_original) if r.amount_original else None, r.currency_original)
```

with:

```python
                # format_amount already returns "—" for a None amount, so
                # to_float_or_none can be passed straight through. format_currency
                # does NOT guard None itself, so its call keeps an explicit check.
                orig = format_amount(to_float_or_none(r.amount_original), r.currency_original)
                amount_ils_f = to_float_or_none(r.amount_ils)
                ils = format_currency(amount_ils_f, "₪") if amount_ils_f is not None else "—"
                cells = [date_s, inv_num, vendor, desc, orig, ils]
            else:
                amt = format_amount(to_float_or_none(r.amount_original), r.currency_original)
```

- [ ] **Step 7: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass (behavior-preserving today, since amounts are never actually zero yet — this only changes behavior for a hypothetical future zero amount)

- [ ] **Step 8: Commit**

```bash
git add orchestrator/app/utils/invoice_amount.py orchestrator/app/pipeline/pipeline.py orchestrator/app/export/generators/invoice.py orchestrator/tests/test_invoice_amount.py
git commit -m "fix: add to_float_or_none, replace truthy-check float conversions

float(x) if x else None silently treats a valid zero the same as missing.
Currently safe only because invoice amounts can't be zero — the exact
kind of cross-file invariant that already drifted once this session."
```

---

## Task 8: Consolidate the R2 invoice metadata sidecar dict

**Problem:** The R2 JSON sidecar dict (18 fields) is hand-written independently in `app/pipeline/pipeline.py` (lines 218-237, from local extraction variables, at initial ingestion) and `app/pipeline/storage.py`'s `sync_invoice_sidecar` (lines 147-166, from the `Invoice` ORM object, re-synced after any field correction). `storage.py`'s own module docstring states R2 is meant to be a source of truth the DB can be rebuilt from — the two dicts must stay field-for-field identical, and a new `Invoice` column is easy to add to one and forget the other.

**Key insight verified against current code:** by the time `pipeline.py` needs to build the sidecar dict (line 213+), it has *already* constructed the full `Invoice` ORM object (`invoice = Invoice(...)`, lines 181-202) — so both call sites can share a function taking an `Invoice` instance; no adapter/protocol is needed for raw-locals-vs-ORM-object, since pipeline.py already has the ORM object in scope.

**Files:**
- Modify: `orchestrator/app/pipeline/storage.py` (add the shared function, simplify `sync_invoice_sidecar`)
- Modify: `orchestrator/app/pipeline/pipeline.py` (use the shared function)
- Test: `orchestrator/tests/test_storage.py` (check if this file exists; if not, add to whatever test file already covers `app/pipeline/storage.py`, or create it)

- [ ] **Step 1: Write the failing test**

Find or create the test file covering `app/pipeline/storage.py` (search `orchestrator/tests/` for `sync_invoice_sidecar` or `upload_metadata` usage first). Add:

```python
def test_invoice_to_sidecar_dict_has_all_18_fields():
    """Regression: both pipeline.py (initial ingestion) and storage.py
    (post-correction re-sync) must build the sidecar from this one function,
    not two hand-written, independently-maintained dicts that must
    coincidentally stay field-for-field identical."""
    from datetime import date, datetime, timezone
    from decimal import Decimal
    from app.db.models import Invoice
    from app.pipeline.storage import invoice_to_sidecar_dict

    invoice = Invoice(
        id="inv-1", group_id="123@g.us", message_id="msg-1", image_hash="hash-1",
        submitted_by="972501@s.whatsapp.net",
        received_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        invoice_date=date(2026, 7, 14), invoice_number="INV-1", vendor="Acme",
        description="Widgets", amount_original=Decimal("100"), currency_original="ILS",
        amount_ils=Decimal("100"), exchange_rate=Decimal("1"), rate_source="boi",
        extraction_confidence=0.9, flagged=False, flag_reason=None,
    )
    d = invoice_to_sidecar_dict(invoice)
    assert d["invoice_id"] == "inv-1"
    assert d["amount_original"] == 100.0
    assert d["invoice_date"] == "2026-07-14"
    assert d["received_at"] == "2026-07-14T00:00:00+00:00"
    assert set(d.keys()) == {
        "invoice_id", "group_id", "message_id", "image_hash", "submitted_by",
        "received_at", "invoice_date", "invoice_number", "vendor", "description",
        "amount_original", "currency_original", "amount_ils", "exchange_rate",
        "rate_source", "extraction_confidence", "flagged", "flag_reason",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && python -m pytest tests/ -k invoice_to_sidecar_dict -v`
Expected: FAIL with `ImportError: cannot import name 'invoice_to_sidecar_dict'`

- [ ] **Step 3: Add `invoice_to_sidecar_dict` to `app/pipeline/storage.py`, simplify `sync_invoice_sidecar`**

Add this function above `sync_invoice_sidecar`:

```python
def invoice_to_sidecar_dict(invoice) -> dict:
    """Build the R2 JSON sidecar dict for an invoice — the single source of
    truth for its shape, used both at initial ingestion (pipeline.py, which
    already has a fully-constructed Invoice object in scope by the time it
    uploads the sidecar) and whenever a field is corrected afterward
    (sync_invoice_sidecar below). R2 is meant to be a rebuild-the-DB-from-R2
    source of truth (see module docstring) — the two call sites previously
    hand-built this dict independently in two files, with no guarantee a
    new Invoice column would be added to both.
    """
    from app.utils.invoice_amount import to_float_or_none
    return {
        "invoice_id":            invoice.id,
        "group_id":              invoice.group_id,
        "message_id":            invoice.message_id,
        "image_hash":            invoice.image_hash,
        "submitted_by":          invoice.submitted_by,
        "received_at":           invoice.received_at.isoformat() if invoice.received_at else None,
        "invoice_date":          invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "invoice_number":        invoice.invoice_number,
        "vendor":                invoice.vendor,
        "description":           invoice.description,
        "amount_original":       to_float_or_none(invoice.amount_original),
        "currency_original":     invoice.currency_original,
        "amount_ils":            to_float_or_none(invoice.amount_ils),
        "exchange_rate":         to_float_or_none(invoice.exchange_rate),
        "rate_source":           invoice.rate_source,
        "extraction_confidence": invoice.extraction_confidence,
        "flagged":               invoice.flagged,
        "flag_reason":           invoice.flag_reason,
    }
```

Replace `sync_invoice_sidecar`'s body:

```python
async def sync_invoice_sidecar(invoice) -> None:
    """Re-upload the R2 JSON sidecar for an invoice after any field correction.

    Silently logs and returns if the invoice has no r2_key (image upload failed
    at ingest time) so corrections still apply to the DB without crashing.
    """
    if not invoice.r2_key:
        return
    try:
        await upload_metadata(invoice.r2_key, {
            "invoice_id":            invoice.id,
            "group_id":              invoice.group_id,
            "message_id":            invoice.message_id,
            "image_hash":            invoice.image_hash,
            "submitted_by":          invoice.submitted_by,
            "received_at":           invoice.received_at.isoformat() if invoice.received_at else None,
            "invoice_date":          invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            "invoice_number":        invoice.invoice_number,
            "vendor":                invoice.vendor,
            "description":           invoice.description,
            "amount_original":       float(invoice.amount_original) if invoice.amount_original else None,
            "currency_original":     invoice.currency_original,
            "amount_ils":            float(invoice.amount_ils) if invoice.amount_ils else None,
            "exchange_rate":         float(invoice.exchange_rate) if invoice.exchange_rate else None,
            "rate_source":           invoice.rate_source,
            "extraction_confidence": invoice.extraction_confidence,
            "flagged":               invoice.flagged,
            "flag_reason":           invoice.flag_reason,
        })
    except RuntimeError:
        import logging
        logging.getLogger(__name__).warning(
            "Could not sync R2 sidecar for invoice %s — DB is authoritative", invoice.id
        )
```

with:

```python
async def sync_invoice_sidecar(invoice) -> None:
    """Re-upload the R2 JSON sidecar for an invoice after any field correction.

    Silently logs and returns if the invoice has no r2_key (image upload failed
    at ingest time) so corrections still apply to the DB without crashing.
    """
    if not invoice.r2_key:
        return
    try:
        await upload_metadata(invoice.r2_key, invoice_to_sidecar_dict(invoice))
    except RuntimeError:
        import logging
        logging.getLogger(__name__).warning(
            "Could not sync R2 sidecar for invoice %s — DB is authoritative", invoice.id
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && python -m pytest tests/ -k invoice_to_sidecar_dict -v`
Expected: PASS

- [ ] **Step 5: Use the shared function in `pipeline.py`**

In `orchestrator/app/pipeline/pipeline.py`, replace the `upload_metadata` call block (lines 216-239):

```python
    if r2_key:
        try:
            await upload_metadata(r2_key, {
                "invoice_id":          invoice_id,
                "group_id":            jid,
                "message_id":          message_id,
                "image_hash":          image_hash,
                "submitted_by":        sender,
                "received_at":         datetime.now(timezone.utc).isoformat(),
                "invoice_date":        invoice_date_str,
                "invoice_number":      invoice_number,
                "vendor":              vendor,
                "description":         description,
                "amount_original":     to_float_or_none(amount_original),
                "currency_original":   currency_original,
                "amount_ils":          to_float_or_none(amount_ils),
                "exchange_rate":       to_float_or_none(exchange_rate),
                "rate_source":         rate_source,
                "extraction_confidence": confidence,
                "flagged":             flagged,
                "flag_reason":         flag_reason,
            })
        except RuntimeError:
            logger.warning("Metadata sidecar upload failed for invoice %s — data still in DB", invoice_id)
```

(Note: this shows the dict AFTER Task 7's `to_float_or_none` edit already applied — if Task 7 ran before this task, as planned, the code you're looking at in the actual file will already have `to_float_or_none` calls, not the original `float(x) if x else None`. Match against whatever is actually in the file at this point, the transformation described below is the same either way.)

with:

```python
    if r2_key:
        try:
            from app.pipeline.storage import invoice_to_sidecar_dict
            await upload_metadata(r2_key, invoice_to_sidecar_dict(invoice))
        except RuntimeError:
            logger.warning("Metadata sidecar upload failed for invoice %s — data still in DB", invoice_id)
```

(`invoice` here is the `Invoice(...)` object already constructed at line 181-202 in this same function, already added to the session and committed at line 204-206, above this block — confirm this ordering is unchanged before making this edit; if anything about invoice's committed state matters here, i.e. some field only gets its final value after `db.commit()`, note that `db.commit()` doesn't mutate any of `invoice`'s Python-side attribute values, only persists them, so `invoice.amount_original` etc. read the same before/after commit.)

- [ ] **Step 6: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass. If any existing test asserted the sidecar dict's exact shape by mocking `upload_metadata` and checking its call args, that test's assertion should still pass since the field set and values are unchanged — only the *source* of the dict changed (from local variables to the ORM object's attributes, which hold the same values).

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/pipeline/storage.py orchestrator/app/pipeline/pipeline.py orchestrator/tests/
git commit -m "fix: consolidate R2 invoice sidecar dict into invoice_to_sidecar_dict

Was hand-written independently in pipeline.py (ingestion) and storage.py
(post-correction re-sync) — a new Invoice column was easy to add to one and
forget the other, silently producing inconsistent sidecar shapes."
```

---

## Task 9: Consolidate automation executor's "tool not available" message

**Problem:** `app/automation/executor.py`'s `_run_tool` (lines 67-74) and `_run_workflow`'s per-step check (lines 114-125) both build a near-identical "tool not available" message with slightly different wording. Low risk (cosmetic drift only), but trivial to fix while in this area.

**Files:**
- Modify: `orchestrator/app/automation/executor.py`
- Test: `orchestrator/tests/test_automation_evaluators.py` or wherever `AutomationExecutor` is already tested (search first)

- [ ] **Step 1: Add the shared helper**

In `orchestrator/app/automation/executor.py`, add near the top of the class (or as a module-level function, matching this file's existing style — check whether other small helpers in this file are methods or module-level functions first):

```python
    @staticmethod
    def _tool_unavailable_message(tool_name: str, *, step: int | None = None) -> str:
        prefix = f"step {step + 1}: " if step is not None else ""
        return (
            f"⚙️ Automation could not run{' workflow ' + prefix if step is not None else ': '}"
            f"tool '{tool_name}' is not available. Ask your administrator to enable it."
        )
```

Check this produces the same two message strings currently hardcoded (`f"⚙️ Automation could not run: tool '{tool_name}' is not available. Ask your administrator to enable it."` and `f"⚙️ Automation workflow could not run step {i + 1}: tool '{tool_name}' is not available. Ask your administrator to enable it."`) — if the string-building logic above doesn't cleanly reproduce both, simplify to two thin wrapper calls instead of one clever conditional:

```python
    @staticmethod
    def _tool_unavailable_message(tool_name: str) -> str:
        return f"⚙️ Automation could not run: tool '{tool_name}' is not available. Ask your administrator to enable it."

    @staticmethod
    def _tool_unavailable_workflow_message(tool_name: str, step: int) -> str:
        return f"⚙️ Automation workflow could not run step {step + 1}: tool '{tool_name}' is not available. Ask your administrator to enable it."
```

Use whichever of these two shapes is simpler to review — this task is intentionally low-ceremony (no dedicated test required; it's a pure string-building refactor with no behavior change, verified by the full suite in Step 3).

- [ ] **Step 2: Use the helper at both call sites**

Replace:
```python
        if not reg.has_tool(tool_name):
            logger.warning("Automation: tool %r not in registry (rule group %s)", tool_name, group_jid)
            await send_message(
                group_jid,
                f"⚙️ Automation could not run: tool '{tool_name}' is not available. "
                f"Ask your administrator to enable it.",
            )
            return
```
with:
```python
        if not reg.has_tool(tool_name):
            logger.warning("Automation: tool %r not in registry (rule group %s)", tool_name, group_jid)
            await send_message(group_jid, self._tool_unavailable_message(tool_name))
            return
```

Replace:
```python
            if not reg.has_tool(tool_name):
                logger.warning(
                    "Automation workflow step %d: tool %r not in registry (group %s)",
                    i, tool_name, group_jid,
                )
                await send_message(
                    group_jid,
                    f"⚙️ Automation workflow could not run step {i + 1}: "
                    f"tool '{tool_name}' is not available. "
                    f"Ask your administrator to enable it.",
                )
                return
```
with:
```python
            if not reg.has_tool(tool_name):
                logger.warning(
                    "Automation workflow step %d: tool %r not in registry (group %s)",
                    i, tool_name, group_jid,
                )
                await send_message(group_jid, self._tool_unavailable_workflow_message(tool_name, i))
                return
```

- [ ] **Step 3: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass unchanged (identical message text, same behavior)

- [ ] **Step 4: Commit**

```bash
git add orchestrator/app/automation/executor.py
git commit -m "refactor: consolidate automation executor's tool-unavailable messaging"
```

---

## Task 10: Test suite — shared `SessionCM`

**Problem:** A `SessionLocal`-patching context-manager class is redefined locally in ~19 test files, already in at least 2 different behavioral shapes (some never close the session on `__exit__`, one factory-based variant does close). Files with a local copy (verify this list against current HEAD by grepping `class _CM\b\|class _SessionCM\b` in `orchestrator/tests/` before starting, since exact files may have shifted since the audit): `test_accounting_enhancements.py`, `test_accounting_tools.py`, `test_admin_api.py`, `test_admin_tool_management.py` (multiple local copies in one file), `test_agent_context.py`, `test_automation_scheduler.py`, `test_automation_tools.py`, `test_email_allowlist.py`, `test_export_tool.py` (`_CM` and `_CM2`), `test_invoice_tools.py`, `test_multi_confirmation.py`, `test_scheduler.py`, `test_split_tools.py`, `test_userprofile_routing.py`.

**Files:**
- Modify: `orchestrator/tests/conftest.py`
- Modify: every file listed above (delete local class, import shared one)

- [ ] **Step 1: Add the shared `SessionCM` to `conftest.py`**

First, read `orchestrator/tests/conftest.py` in full (it's short — just the `db` fixture) to confirm exact current content before appending. Add:

```python
class SessionCM:
    """Wrap a SQLAlchemy session (or a factory producing one) as a context
    manager, for patching SessionLocal in code under test. Closes the
    session on exit — the more correct of the ~4 slightly different local
    copies of this same class that existed across the test suite before
    consolidation; some never closed the session.

    Usage: patch("app.module.SessionLocal", side_effect=lambda: SessionCM(db))
    or, if the code under test expects SessionLocal to be a zero-arg
    callable itself: patch("app.module.SessionLocal", new=lambda: SessionCM(db)).
    """
    def __init__(self, session_or_factory):
        self._session_or_factory = session_or_factory
        self._session = None
        self._owns_session = False

    def __enter__(self):
        if callable(self._session_or_factory):
            self._session = self._session_or_factory()
            self._owns_session = True
        else:
            self._session = self._session_or_factory
        return self._session

    def __exit__(self, *exc):
        if self._owns_session and self._session is not None:
            self._session.close()
```

- [ ] **Step 2: Run the full test suite to confirm the addition alone doesn't break anything**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass (pure addition, nothing uses it yet)

- [ ] **Step 3: Commit the addition**

```bash
git add orchestrator/tests/conftest.py
git commit -m "test: add shared SessionCM to conftest.py"
```

- [ ] **Step 4: Migrate each file, one at a time, running the full suite after each**

For each file in the list above: read its local `_CM`/`_SessionCM` class definition and every place it's used (both as `patch(..., return_value=_CM(db))` and `patch(..., side_effect=lambda: _CM(db))` — the two existing shapes take the session differently, check which this file uses). Delete the local class definition. Add `from tests.conftest import SessionCM` (check this file's existing relative-import style for conftest — since `conftest.py` is auto-discovered by pytest, some files may not import it explicitly at all today, only implicitly via the `db` fixture; if a file needs the CLASS itself, not just the fixture, it needs an explicit import — verify how pytest conftest exports work in this specific project setup by checking how the `db` fixture itself gets used, since `SessionCM` isn't a fixture, it's a plain class, so it likely needs `from conftest import SessionCM` or `from tests.conftest import SessionCM` depending on how tests are invoked — check an existing cross-file test import in this test suite to confirm the right import path before applying it to all 14+ files). Replace every `_CM(...)`/`_SessionCM(...)` reference with `SessionCM(...)`, preserving whether the surrounding `patch(...)` call used `return_value=` or `side_effect=lambda: ...` exactly as before (don't change that shape, just swap the class).

After each individual file's migration, run: `cd orchestrator && python -m pytest tests/<that_file>.py -q` to confirm it still passes standalone, then run the full suite: `cd orchestrator && python -m pytest -q` before moving to the next file. Commit after each file (or batch 2-3 small/similar files into one commit if that's cleaner — use judgment, but don't batch all 14+ into one commit, since a mistake in one file's migration should be bisectable).

- [ ] **Step 5: Final full-suite verification and commit**

Run: `cd orchestrator && python -m pytest -q`
Expected: same total test count as before this task started, all passing.

```bash
git add orchestrator/tests/
git commit -m "test: migrate all SessionLocal-patching to shared conftest.SessionCM

~19 test files each redefined this same context-manager class locally, in
at least 2 different behavioral shapes (some never closed the session)."
```

---

## Task 11: Test suite — shared entity-seeding fixtures

**Problem:** `_seed_group`/`_seed_household`/`_seed_blueprint`/`_seed_user`-style helpers are redefined locally in 6+ files (verify against current HEAD: `test_account_service.py`, `test_admin_api.py`, `test_automation_scheduler.py`, `test_export_tool.py`, `test_participants.py`, `test_automation_tools.py` — the last one has two competing copies in the same file). Some tests (`test_command_handler.py`, `test_custom_instructions.py`) insert a `GroupRegistry` row referencing a `Blueprint` that's never created in that test — silently fine only because SQLite doesn't enforce foreign keys by default.

**Files:**
- Modify: `orchestrator/tests/conftest.py`
- Modify: the 6+ files listed above

- [ ] **Step 1: Add shared seeding fixtures to `conftest.py`**

```python
def seed_blueprint(db, id="test_bp", **overrides):
    from app.db.models import Blueprint
    existing = db.query(Blueprint).filter_by(id=id).first()
    if existing:
        return existing
    defaults = dict(display_name="Test Blueprint", system_prompt="p", tools_enabled="[]")
    defaults.update(overrides)
    bp = Blueprint(id=id, **defaults)
    db.add(bp)
    db.commit()
    return bp


def seed_group(db, jid, blueprint_id=None, **overrides):
    from app.db.models import GroupRegistry
    if blueprint_id is None:
        blueprint_id = seed_blueprint(db).id
    else:
        seed_blueprint(db, id=blueprint_id)  # auto-seed if missing — makes the
        # FK-ordering bug class (GroupRegistry referencing a never-created
        # Blueprint, silently fine only because SQLite doesn't enforce FKs
        # by default) structurally impossible.
    defaults = dict(group_type="personal")
    defaults.update(overrides)
    g = GroupRegistry(group_jid=jid, blueprint_id=blueprint_id, **defaults)
    db.add(g)
    db.commit()
    return g


def seed_household(db, phone, group_jid, blueprint_id="family_accounting"):
    from app.db.models import Household, HouseholdMember
    seed_group(db, group_jid, blueprint_id=blueprint_id)
    h = Household(name="Test Family")
    db.add(h)
    db.flush()
    m = HouseholdMember(household_id=h.id, phone=phone, private_group_jid=group_jid)
    db.add(m)
    db.commit()
    return h, m
```

Before writing this, read each of the 6+ files' existing `_seed_blueprint`/`_seed_group`/`_seed_household` definitions first (they're not identical — check field names, defaults, and signatures across all of them) and adjust the shared versions above to be a strict superset of what every caller needs (accepting `**overrides` for anything a specific test needs to customize beyond the defaults). If any existing caller needs a parameter these shared functions don't support, add it as a keyword arg with a sensible default rather than breaking that caller.

- [ ] **Step 2: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass (pure addition)

- [ ] **Step 3: Commit the addition**

```bash
git add orchestrator/tests/conftest.py
git commit -m "test: add shared seed_blueprint/seed_group/seed_household to conftest.py"
```

- [ ] **Step 4: Migrate each file, one at a time**

For each of the 6+ files: compare its local seeding helper(s) against the shared ones added above. If behaviorally equivalent (same defaults, same fields), delete the local helper and its call sites become calls to the shared one (import it — check this project's convention for importing from conftest, same note as Task 10 Step 4). If a local helper does something the shared one doesn't support, either extend the shared one (preferred, if the extra behavior is generically useful) or leave that specific file's helper as a thin wrapper around the shared one that adds the extra behavior (acceptable, but note in a comment why it's not fully consolidated).

For `test_automation_tools.py` specifically: it has two competing copies (`_seed_group_for_orm`, blueprint_id `"invoice_curator"`, and `_seed_group`, blueprint_id `"family_accounting"`) doing the same Blueprint+GroupRegistry insert with only the id string different — replace both with calls to the shared `seed_group(db, jid, blueprint_id="invoice_curator")` / `seed_group(db, jid, blueprint_id="family_accounting")`.

For `test_command_handler.py` and `test_custom_instructions.py` specifically: these currently insert `GroupRegistry` without ever creating the referenced `Blueprint` — using the shared `seed_group` (which auto-seeds the blueprint) fixes this silently-relies-on-no-FK-enforcement gap as a side effect of the migration.

After each file: run `cd orchestrator && python -m pytest tests/<that_file>.py -q`, then the full suite, before moving on. Commit per-file or in small batches, same guidance as Task 10.

- [ ] **Step 5: Final full-suite verification and commit**

Run: `cd orchestrator && python -m pytest -q`
Expected: same total test count as before this task started (or +0, since no new tests were added — only fixtures consolidated), all passing.

```bash
git add orchestrator/tests/
git commit -m "test: migrate entity-seeding helpers to shared conftest fixtures

6+ files redefined near-identical Blueprint/GroupRegistry/Household seeding
locally (test_automation_tools.py had two competing copies in one file).
Two files relied on SQLite not enforcing FKs — shared seed_group auto-seeds
the referenced Blueprint, closing that gap."
```

---

## Task 12: Test suite — shared `make_invoice` factory

**Problem:** Inline `Invoice(...)` construction with the full required-NOT-NULL-field set (`message_id`, `image_hash`) is duplicated in `test_automation_evaluators.py`, `test_export_tool.py`, `test_invoice_tools.py` — the exact pair of columns whose addition already broke multiple tests independently earlier this session, precisely because there was no single shared factory to update.

**Files:**
- Modify: `orchestrator/tests/conftest.py`
- Modify: `test_automation_evaluators.py`, `test_export_tool.py`, `test_invoice_tools.py`

- [ ] **Step 1: Add `make_invoice` to `conftest.py`**

Read `app/db/models.py`'s `Invoice` class first to confirm the exact current set of NOT-NULL columns before writing defaults (it should be `id`, `group_id`, `message_id`, `image_hash` per this session's earlier work — verify nothing else has been added since).

```python
def make_invoice(db, **overrides):
    from datetime import date
    from decimal import Decimal
    from app.db.models import Invoice
    defaults = dict(
        id=f"inv-{uuid.uuid4().hex[:8]}",
        group_id="123@g.us",
        message_id=f"msg-{uuid.uuid4().hex[:8]}",
        image_hash=f"hash-{uuid.uuid4().hex[:8]}",
        invoice_date=date.today(),
        vendor="Test Vendor",
        amount_original=Decimal("100"),
        currency_original="ILS",
        amount_ils=Decimal("100"),
    )
    defaults.update(overrides)
    invoice = Invoice(**defaults)
    db.add(invoice)
    db.commit()
    return invoice
```

(Add `import uuid` to `conftest.py`'s imports if not already present.)

- [ ] **Step 2: Run the full test suite**

Run: `cd orchestrator && python -m pytest -q`
Expected: all pass (pure addition)

- [ ] **Step 3: Commit the addition**

```bash
git add orchestrator/tests/conftest.py
git commit -m "test: add shared make_invoice factory to conftest.py"
```

- [ ] **Step 4: Migrate the 3 call sites**

Read each of `test_automation_evaluators.py`, `test_export_tool.py`, `test_invoice_tools.py`'s `Invoice(...)` construction(s) — they may set different specific fields for their own test's purposes (e.g. `flagged=True`, a specific `vendor`). Replace each with `make_invoice(db, **the specific overrides that test needs)`, preserving whatever field values that test actually asserts on afterward (don't change test behavior — only replace the boilerplate NOT-NULL-field defaults with the shared factory's defaults, keeping every field the test explicitly cares about as an explicit override).

After each file: run `cd orchestrator && python -m pytest tests/<that_file>.py -q`, then the full suite.

- [ ] **Step 5: Final full-suite verification and commit**

Run: `cd orchestrator && python -m pytest -q`
Expected: same total test count as before this task started, all passing.

```bash
git add orchestrator/tests/
git commit -m "test: migrate inline Invoice(...) construction to shared make_invoice factory

3 files each hand-built the full NOT-NULL field set — exactly the columns
(message_id, image_hash) whose earlier addition broke several tests
independently instead of in one place."
```

---

## Task 13: Consolidate "net a directed pair into one signed line" (found during Task 2's review)

**Problem:** Task 2's code-quality review found that the "given two directed amounts between A and B, produce one signed (debtor, creditor, amount) or nothing if settled" pattern — the exact thing Task 2 just added to `_exec_get_debt_summary` — was *already* duplicated 5 other times before this plan even started:
- `app/tools/accounting_tools.py`'s `_exec_get_balance`, 3 separate inline copies (search for the `if net > Decimal("0"): ... elif net < Decimal("0"): ...` shape — there are three near-identical occurrences in that one function: the two-phone case, the household-vs-individual case, and the per-partner loop case).
- `app/accounting/account_service.py` (its own sign/label resolution, ~lines 84-86 — read the file to find the exact current lines, since line numbers may have shifted).
- `app/tools/accounting_export.py` (~lines 158-160 — same caveat).

Task 2 added a *sixth* variant of this same "resolve sign, pick debtor/creditor, drop if zero" logic instead of extracting a shared helper — worth fixing now that it's freshly visible, rather than letting a 7th copy appear later.

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py` (add the helper, migrate 4 call sites: the 3 in `_exec_get_balance` + the 1 just added to `_exec_get_debt_summary`)
- Modify: `orchestrator/app/accounting/account_service.py`
- Modify: `orchestrator/app/tools/accounting_export.py`
- Test: `orchestrator/tests/test_accounting_tools.py`

- [ ] **Step 1: Read every call site first, in full, before writing the helper**

This task was added retroactively after Task 2 was already implemented and reviewed — unlike Tasks 1-12, the exact current code for each call site has NOT been re-verified against HEAD as part of writing this plan. Before writing anything, read `app/tools/accounting_tools.py`'s `_exec_get_balance` and `_exec_get_debt_summary` in full, `app/accounting/account_service.py` around its sign/label resolution, and `app/tools/accounting_export.py` around its sign/label resolution, to confirm each one actually matches the "resolve sign → pick debtor/creditor → format or drop if zero" shape described above. If any of these turns out NOT to match cleanly (e.g. the label/formatting conventions differ enough that forcing one shared helper would be awkward), it's fine to consolidate only the subset that genuinely fits and note in the commit message which call sites were left alone and why — don't force a bad abstraction just to hit "5 call sites."

- [ ] **Step 2: Write the failing test for the shared helper**

Design the helper's signature based on what you find in Step 1 — a reasonable starting point (adjust based on what the real call sites need):

```python
def net_pair(a: str, b: str, a_owes_b: Decimal, b_owes_a: Decimal) -> tuple[str, str, Decimal] | None:
    """Net two directed amounts between two phones into one signed
    (debtor, creditor, amount), or None if they fully offset (zero net).
    debtor/creditor are chosen from (a, b) by which direction nets positive."""
```

Write a test in `orchestrator/tests/test_accounting_tools.py` covering: A nets positive, B nets positive (reversed), exact zero (returns None).

- [ ] **Step 3: Run test to verify it fails, implement, verify it passes**

Standard TDD — this step intentionally has no pre-written implementation, since Step 1 determines the final shape.

- [ ] **Step 4: Migrate the identified call sites, run the full suite after each file**

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/tools/accounting_tools.py orchestrator/app/accounting/account_service.py orchestrator/app/tools/accounting_export.py orchestrator/tests/test_accounting_tools.py
git commit -m "refactor: extract net_pair helper, consolidating 6 copies of sign/debtor/creditor resolution

Found during Task 2's code-quality review: the same 'net two directed
amounts into one signed line' logic Task 2 added to get_debt_summary
already existed 5 other times (3 in get_balance alone)."
```

---

## Final Steps (after all 12 tasks)

- [ ] Run the full test suite one final time: `cd orchestrator && python -m pytest -q` — expect all passing, total count ≥ 440 (the baseline) plus every new test added across tasks 1-12.
- [ ] Use the `superpowers:finishing-a-development-branch` skill to merge `fix/copy-paste-consolidation` back into `feat/whatsapp-agent-engine` and clean up the worktree.
