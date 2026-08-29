# Household Admin vs. Group Admin — Design

## Problem

There is exactly one authorization tier in this app today: the global `AdminNumbers` table (currently two people, Eran and Sivan), checked identically regardless of blueprint or group. WhatsApp's own native per-group admin flag (`isAdmin`, forwarded by the bridge on every message) exists in the data but is deliberately never trusted for authorization — the only place it's used is the admin panel's group-listing display.

This collapses two genuinely different questions into one flag: "can this person amend household financial records / see everyone's data" and "can this person change how this specific chat behaves" end up requiring the exact same global admin status, with no way to grant one without the other. Flagging someone admin in the web interface to solve a narrow, per-group problem (e.g. "let Roni set her own report language") would also hand them full control over every other group they're in, including the shared invoice_curator group — far broader than intended.

## Non-goals

- No changes to invoice_curator's authorization at all. It keeps its single `is_admin` gate, unchanged, on every tool it has today. The new `is_group_admin` flag is computed but simply never used there.
- No new bridge calls, no new caching layer. The bridge already computes a safely-cached (5 min TTL, invalidated on any `group-participants.update` event), non-forgeable (webhook is `WEBHOOK_SECRET`-authenticated) admin flag per message via `bridge/src/adminCache.js`, and already forwards it as `isAdmin` in every webhook payload. This design only starts trusting a field that already exists and already flows through — it adds no new moving parts to the bridge.
- Not touching `GroupParticipant.is_household` / the stale `set_household` tool reference in `list_participants`'s description — tracked as a separate, unrelated cleanup item (`task_c22a40f0`).

## Trust model

`WebhookPayload`'s existing `is_admin` field (Pydantic alias `"isAdmin"`, sourced from the bridge's `adminCache.isGroupAdmin`) is renamed to `is_group_admin` for clarity — the wire alias stays `"isAdmin"` so no bridge change is needed. This field is now trusted directly; no independent re-verification call is added, since the bridge's cache + invalidation-on-membership-change already provides that safety, and the webhook itself can't be forged by anyone without the shared secret.

Verified against live data before finalizing this design: in both existing "personal" (private 1:1) family_accounting groups, the real human participant is WhatsApp's own designated admin of their group (as the creator), while the bot's own participant entry is not. In the one existing "shared" household group, only Eran (not Sivan) is currently a WhatsApp-native admin — confirming the hierarchy rule below is required, not just tidy.

## Permission hierarchy

A strict superset, not two independent flags:

```
user  ⊆  group_admin  ⊆  admin (household)
```

- `is_admin` (household admin, unchanged `AdminNumbers` mechanism) can do everything — every `user`- and `group_admin`-tier action, plus the household-only ones.
- `is_group_admin` (WhatsApp-native admin of the specific group, no household admin) can do `user`- and `group_admin`-tier actions only.
- Neither flag: `user`-tier only.

Household admin deliberately supersedes group admin rather than the two being independent, because a household admin who happens not to be the WhatsApp-designated admin of one particular group (Sivan, in the shared group today) must not lose capabilities she already has.

## Concrete changes

**`ToolRegistry.get_allowed_tool_names`** (`app/tool_registry.py`) gains a third schema `"access"` value, `"group_admin"`, and a new `is_group_admin` parameter:

```python
def get_allowed_tool_names(self, tool_names, is_admin, is_group_admin=False):
    for name in tool_names:
        access = self._tools[name]["schema"].get("access", "user")
        if access == "user":
            allowed
        elif access == "group_admin":
            allowed if (is_admin or is_group_admin)
        elif access == "admin":
            allowed if is_admin
```

**`AgentRunner.run`** gains an `is_group_admin: bool = False` parameter, threaded into the tool-execution `ctx` alongside the existing `is_admin`, so executors that need it directly (not just schema-level filtering) can read `ctx.get("is_group_admin")`.

**`main.py`**: `_process` passes `payload.is_group_admin` through to `agent_runner.run(...)`. No new computation — the value already exists on the payload.

**Tool schema `access` changes** (`app/tools/accounting_tools.py`), `"admin"` → `"group_admin"`:
- `rename_participant`
- `create_report_format`
- `list_report_formats`
- `delete_report_format`

**Runtime check change** (`app/export/tool.py`): the `_persist_language_default` call (added in the previous language-handling redesign, currently ungated) becomes gated on `is_admin or is_group_admin` — an explicit report-language request only becomes the new sticky default when the requester has at least group-admin authority for that group.

**Unchanged, stays on `is_admin`:** `correct_transaction`, `commit_correction`, `get_transaction` (the read prerequisite for correcting), and `export_accounting_report`'s full-household-visibility scoping (`filter_phone = None if is_admin else sender_phone`).

**Prompt wording**: no change needed. The "if an admin explicitly tells you to use a specific language, follow that" line in both prompts stays generic — it's a soft conversational instruction, and the actual enforcement (whether the language change *sticks* as a new default) happens in code via the check above, not via the model's own judgment of who counts as "an admin."

## Testing

- `test_tool_registry.py`: 3-tier `get_allowed_tool_names` behavior — `group_admin`-tier tool visible to `is_admin=True`, visible to `is_group_admin=True`, hidden to neither.
- `test_agent_runner.py`: `is_group_admin` threads into tool-execution `ctx`; invoice_curator turns are unaffected (never pass or use it).
- `test_export_tool.py`: language persistence gated correctly — `is_admin=True` persists, `is_group_admin=True` persists, neither does not.
- `test_accounting_tools.py`: `rename_participant`/report-format tools reachable with `is_group_admin=True` alone, not reachable with neither flag.
- Live verification (matching this session's established pattern): rerun the sim harness for a family_accounting-shaped scenario with a mocked `is_group_admin`, confirming a non-household-admin group-admin can rename a participant / set report language, and cannot correct a transaction.
